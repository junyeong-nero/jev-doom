"""Episode loop: encode -> Jev -> act, with JSONL logging.

Usage:
    uv run python -m doom.play --scenario defend --episodes 2
    uv run python -m doom.play --scenario defend --visible   # watch it play
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import httpx

from . import config as C
from .doom_env import make_game
from .encoder import encode
from .policy import decide, to_action


def run_episode(game, client, log, scenario: str = "defend",
                frames: list | None = None) -> dict:
    game.new_episode()
    decisions, latencies, shots = 0, [], 0
    last_turn = None
    while not game.is_episode_finished():
        state = game.get_state()
        if state is None:
            break
        if frames is not None and state.screen_buffer is not None:
            frames.append(state.screen_buffer.transpose(1, 2, 0).copy())
        snapshot = encode(state, list(state.game_variables))
        try:
            answers, usage, ms = decide(client, snapshot)
        except Exception as e:  # Jev hiccup -> hold, keep episode alive
            print(f"  [warn] jev error: {e}, holding", flush=True)
            game.make_action([0, 0, 0], C.TURN_TICS)
            continue
        action, tics, reason = to_action(answers, snapshot, last_turn, scenario)
        if action[2]:
            shots += 1
            game.make_action(action, tics)
            game.make_action([0, 0, 0], C.RELEASE_TICS)  # release: re-press per bullet
        else:
            game.make_action(action, tics)
        if action in ([1, 0, 0], [0, 1, 0]):
            last_turn = action
        decisions += 1
        latencies.append(ms)
        log({
            "aim": answers["aim"]["choice"],
            "aim_conf": round(answers["aim"]["confidence"], 3),
            "fire": round(answers["fire"]["noul"], 3),
            "danger": round(answers["danger"]["score"], 2),
            "action": action, "reason": reason, "ms": round(ms),
            "in_tok": usage.get("input_tokens", 0),
            "out_tok": usage.get("output_tokens", 0),
            "hp": snapshot["player"]["health"],
            "ammo": snapshot["player"]["ammo"],
            "kills": snapshot["player"]["kills"],
        })
        if decisions == 1 or decisions % 10 == 0:
            print(f"  d{decisions}: aim={answers['aim']['choice']} "
                  f"fire={answers['fire']['noul']:.2f} "
                  f"danger={answers['danger']['score']:.2f} "
                  f"hp={snapshot['player']['health']:.0f} "
                  f"ammo={snapshot['player']['ammo']:.0f} "
                  f"kills={snapshot['player']['kills']} "
                  f"[{reason}, {ms:.0f}ms]", flush=True)
    total = game.get_total_reward()
    return {
        "decisions": decisions,
        "shots": shots,
        "avg_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "reward": total,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="defend",
                    choices=["defend", "basic", "simple"])
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--timeout", type=int, default=2100)
    ap.add_argument("--record", action="store_true",
                    help="save screen frames to runs/<ts>_frames.npz")
    args = ap.parse_args()

    if not C.API_KEY:
        sys.exit("no API key: set TYPESAFE_API_KEY or JEV_APIKEY in .env")

    run_dir = Path("runs")
    run_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    game = make_game(args.scenario, visible=args.visible, timeout_tics=args.timeout)
    summaries = []
    try:
        with httpx.Client(timeout=25) as client:
            for ep in range(args.episodes):
                path = run_dir / f"{ts}_{args.scenario}_ep{ep}.jsonl"
                print(f"episode {ep} -> {path}", flush=True)
                frames = [] if args.record else None
                with open(path, "w") as f:
                    def log(obj, f=f):
                        f.write(json.dumps(obj) + "\n")
                    s = run_episode(game, client, log, args.scenario, frames)
                if frames:
                    import numpy as np
                    fpath = run_dir / f"{ts}_{args.scenario}_ep{ep}_frames.npz"
                    np.savez_compressed(fpath, frames=np.stack(frames))
                    print(f"saved {len(frames)} frames -> {fpath}", flush=True)
                import vizdoom as vzd
                s.update({
                    # post-episode ground truth (log lines are pre-action snapshots)
                    "hp": float(game.get_game_variable(vzd.GameVariable.HEALTH)),
                    "ammo": float(game.get_game_variable(vzd.GameVariable.AMMO2)),
                    "kills": int(game.get_game_variable(vzd.GameVariable.KILLCOUNT)),
                })
                summaries.append(s)
                print(f"episode {ep} done: {s}", flush=True)
    finally:
        game.close()

    print("SUMMARY:", json.dumps(summaries, indent=1))


if __name__ == "__main__":
    main()
