"""Episode loop: encode -> Jev -> act, with JSONL logging.

1-flight async: the game loop never blocks on the API. While the next
decide() runs in a background thread, the current action keeps being
applied; the fresh action swaps in on arrival.

Usage:
    uv run python -m doom.play --scenario defend --episodes 2
    uv run python -m doom.play --scenario defend --visible   # watch it play
"""
import argparse
import datetime
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from . import config as C
from .doom_env import make_game
from .encoder import encode
from .policy import decide, reset_episode, to_action

#: Extension pacing: while the next decision is in flight, the last safe
#: action is held in 2-tic chunks paced at ~real-time Doom speed (35 tics/s),
#: so a slow API costs game time at the live-play rate instead of spinning
#: hundreds of tics through an instant emulator while waiting.
_EXT_PACE_S = 2 / 35


def _step(game, action: list, tics: int, frames: list | None,
          pace: bool = False) -> None:
    """Apply a hold in 2-tic chunks, capturing frames along the way.

    Same buttons for the same total tics (no behavior change), but video
    gets a frame every 2 game-tics instead of one per Jev decision.
    """
    for done in range(0, tics, 2):
        game.make_action(action, min(2, tics - done))
        if frames is not None and not game.is_episode_finished():
            st = game.get_state()
            if st is not None and st.screen_buffer is not None:
                frames.append(st.screen_buffer.transpose(1, 2, 0).copy())
        if pace:
            time.sleep(_EXT_PACE_S)


def _extend(game, action: list, frames: list | None, done,
            cycle_attack: bool) -> None:
    """Hold the last safe action until done() is true, ~real-time paced.

    Never blocks on the API: each 2-tic chunk re-checks done() and the
    episode state. cycle_attack re-presses semi-auto fire (press/release),
    matching the sequential fire+release cadence while holding.
    """
    n = len(action)
    press = ([(action, C.FIRE_TICS), ([0] * n, C.RELEASE_TICS)]
             if cycle_attack else [(action, 2)])
    i = 0
    while not done() and not game.is_episode_finished():
        a, t = press[i % len(press)]
        _step(game, a, t, frames, pace=True)
        i += 1


def run_episode(game, client, log, scenario: str = "defend",
                frames: list | None = None) -> dict:
    """1-flight async loop: 1 decide in flight; hold cur action till arrival."""
    game.new_episode()
    reset_episode()
    decisions, latencies, shots = 0, [], 0
    last_turn = None
    prev = None  # feedback for the next snapshot
    atk_idx = C.ATTACK_IDX.get(scenario, 2)
    n_btn = len(game.get_available_buttons())
    cur = [0] * n_btn  # last safe action; warn-hold zeros until d1 arrives
    cycle = False  # re-press semi-auto fire while holding an attack action

    def fresh():
        """Snapshot the live state (+record frame). None if episode over."""
        state = game.get_state()
        if state is None:
            return None
        if frames is not None and state.screen_buffer is not None:
            frames.append(state.screen_buffer.transpose(1, 2, 0).copy())
        return encode(state, list(state.game_variables), last=prev)

    with ThreadPoolExecutor(max_workers=1) as ex:
        snapshot = fresh()
        if snapshot is None:
            return {"decisions": 0, "shots": 0, "avg_ms": 0,
                    "reward": game.get_total_reward()}
        pending = snapshot
        fut = ex.submit(decide, client, snapshot, scenario)
        while not game.is_episode_finished():
            if not fut.done():
                # Slow API -> extend the last safe action, never block.
                _extend(game, cur, frames, fut.done, cycle)
                continue
            try:
                answers, usage, ms = fut.result()
            except Exception as e:  # Jev hiccup -> hold, keep episode alive
                print(f"  [warn] jev error: {e}, holding", flush=True)
                _step(game, [0] * n_btn, C.TURN_TICS, frames)
                snapshot = fresh()
                if snapshot is None:
                    break
                pending = snapshot
                fut = ex.submit(decide, client, snapshot, scenario)
                continue
            snapshot = pending
            action, tics, reason = to_action(answers, snapshot, last_turn,
                                            scenario)
            if action[atk_idx]:
                shots += 1
                _step(game, action, tics, frames)
                _step(game, [0] * len(action),
                      C.RELEASE_TICS, frames)  # release: re-press per bullet
            else:
                _step(game, action, tics, frames)
            cur, cycle = action, (scenario != "corridor" and
                                  bool(action[atk_idx]))
            if action in ([1, 0, 0], [0, 1, 0]):
                last_turn = action
            decisions += 1
            latencies.append(ms)
            # Feedback for the next decision: what the last pick cost/gained.
            prev = {"action": reason,
                    "hp_change": round(snapshot["player"]["health"] - prev_hp, 1)
                    if decisions > 1 else 0,
                    "ammo_used": round(prev_ammo - snapshot["player"]["ammo"], 1)
                    if decisions > 1 else 0,
                    "kills_change": (snapshot["player"]["kills"] - prev_kills)
                    if decisions > 1 else 0}
            prev_hp, prev_ammo, prev_kills = (snapshot["player"]["health"],
                                             snapshot["player"]["ammo"],
                                             snapshot["player"]["kills"])
            if scenario == "corridor":
                picked = answers["action"]["choice"]
                log({
                    "pick": picked,
                    "pick_conf": round(answers["action"]["confidence"], 3),
                    "danger": round(answers["danger"]["score"], 2),
                    "action": action, "reason": reason, "ms": round(ms),
                    "in_tok": usage.get("input_tokens", 0),
                    "out_tok": usage.get("output_tokens", 0),
                    "hp": snapshot["player"]["health"],
                    "ammo": snapshot["player"]["ammo"],
                    "kills": snapshot["player"]["kills"],
                })
            else:
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
                if scenario == "corridor":
                    print(f"  d{decisions}: pick={answers['action']['choice']} "
                          f"danger={answers['danger']['score']:.2f} "
                          f"hp={snapshot['player']['health']:.0f} "
                          f"ammo={snapshot['player']['ammo']:.0f} "
                          f"kills={snapshot['player']['kills']} "
                          f"[{reason}, {ms:.0f}ms]", flush=True)
                else:
                    print(f"  d{decisions}: aim={answers['aim']['choice']} "
                          f"fire={answers['fire']['noul']:.2f} "
                          f"danger={answers['danger']['score']:.2f} "
                          f"hp={snapshot['player']['health']:.0f} "
                          f"ammo={snapshot['player']['ammo']:.0f} "
                          f"kills={snapshot['player']['kills']} "
                          f"[{reason}, {ms:.0f}ms]", flush=True)
            # Pipeline the next decision: it runs while cur is held above.
            snapshot = fresh()
            if snapshot is None:
                break
            pending = snapshot
            fut = ex.submit(decide, client, snapshot, scenario)
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
                    choices=["defend", "basic", "simple", "corridor"])
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
                with open(str(path).replace(".jsonl", ".summary.json"), "w") as f:
                    json.dump({**s, "scenario": args.scenario,
                               "path": str(path)}, f, indent=1)
    finally:
        game.close()

    print("SUMMARY:", json.dumps(summaries, indent=1))


if __name__ == "__main__":
    main()
