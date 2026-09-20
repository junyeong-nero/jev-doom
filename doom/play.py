"""Episode loop: encode -> heuristic -> act, with JSONL logging.

Synchronous geometry baseline on deadly_corridor (skill 5): every
iteration encodes one snapshot and acts on it immediately. No API,
no waiting, no threads.

Usage:
    uv run python -m doom.play --episodes 2
    uv run python -m doom.play --visible   # watch it play
    uv run python -m doom.play --suite     # fixed seed suite (1..5)
    uv run python -m doom.play --seed 1    # single seeded run
"""
import argparse
import datetime
import json
from pathlib import Path

from . import config as C
from .doom_env import make_game
from .encoder import encode
from .policy import danger_of, heuristic_action, reset_episode

SCENARIO = "corridor"

# Fixed seed suite for comparable evaluation. Same seeds + same code
# => same spawn trajectory; report mean/std, not single runs.
SEED_SUITE = [1, 2, 3, 4, 5]


def _step(game, action: list, tics: int, frames: list | None) -> None:
    """Apply a hold in 2-tic chunks, capturing frames along the way."""
    for done in range(0, tics, 2):
        game.make_action(action, min(2, tics - done))
        if frames is not None and not game.is_episode_finished():
            st = game.get_state()
            if st is not None and st.screen_buffer is not None:
                frames.append(st.screen_buffer.transpose(1, 2, 0).copy())


def _aim_of(action: list, fired: bool) -> str:
    if fired:
        return "center"
    if action[4] or action[2]:
        return "left"
    if action[5] or action[3]:
        return "right"
    return "center"


def run_heuristic_episode(game, log, frames: list | None = None,
                          seed: int | None = None) -> dict:
    """Geometry baseline with the same log schema as before."""
    if seed is not None:
        game.set_seed(seed)
    game.new_episode()
    reset_episode()
    decisions, shots = 0, 0
    atk_idx = C.ATTACK_IDX[SCENARIO]
    while not game.is_episode_finished():
        state = game.get_state()
        if state is None:
            break
        if frames is not None and state.screen_buffer is not None:
            frames.append(state.screen_buffer.transpose(1, 2, 0).copy())
        snapshot = encode(state, list(state.game_variables))
        action, tics, reason = heuristic_action(snapshot)
        if action[atk_idx]:
            shots += 1
            _step(game, action, tics, frames)
            _step(game, [0] * len(action), C.RELEASE_TICS, frames)
        else:
            _step(game, action, tics, frames)
        decisions += 1
        fired = bool(action[atk_idx])
        log({
            "aim": _aim_of(action, fired), "aim_conf": 1.0,
            "fire": 1.0 if fired else 0.0,
            "danger": danger_of(snapshot),
            "action": action, "reason": reason, "ms": 0,
            "in_tok": 0, "out_tok": 0,
            "hp": snapshot["player"]["health"],
            "ammo": snapshot["player"]["ammo"],
            "kills": snapshot["player"]["kills"],
            "focus": None,
        })
        if decisions == 1 or decisions % 50 == 0:
            print(f"  d{decisions}: {reason} "
                  f"hp={snapshot['player']['health']:.0f} "
                  f"ammo={snapshot['player']['ammo']:.0f} "
                  f"kills={snapshot['player']['kills']}", flush=True)
    total = game.get_total_reward()
    return {"decisions": decisions, "shots": shots, "avg_ms": 0,
            "reward": total}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--timeout", type=int, default=2100)
    ap.add_argument("--record", action="store_true",
                    help="save screen frames to runs/<ts>_frames.npz")
    ap.add_argument("--seed", type=int, default=None,
                    help="vizdoom RNG seed (set before new_episode). "
                         "With --episodes N, episode ep uses seed+N.")
    ap.add_argument("--suite", action="store_true",
                    help=f"run the fixed seed suite {SEED_SUITE} "
                         "(overrides --episodes/--seed)")
    args = ap.parse_args()

    run_dir = Path("runs")
    run_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    game = make_game(SCENARIO, visible=args.visible, timeout_tics=args.timeout)
    if args.suite:
        seeds: list = list(SEED_SUITE)
    elif args.seed is not None:
        seeds = [args.seed + ep for ep in range(args.episodes)]
    else:
        seeds = [None] * args.episodes
    summaries = []
    try:
        for ep, seed in enumerate(seeds):
            suffix = f"_seed{seed}" if seed is not None else ""
            path = run_dir / f"{ts}_{SCENARIO}_ep{ep}{suffix}_heuristic.jsonl"
            print(f"episode {ep} (seed={seed}) -> {path}", flush=True)
            frames = [] if args.record else None
            with open(path, "w") as f:
                def log(obj, f=f):
                    f.write(json.dumps(obj) + "\n")
                s = run_heuristic_episode(game, log, frames, seed=seed)
            if frames:
                import numpy as np
                fpath = (run_dir /
                         f"{ts}_{SCENARIO}_ep{ep}{suffix}_heuristic_frames.npz")
                np.savez_compressed(fpath, frames=np.stack(frames))
                print(f"saved {len(frames)} frames -> {fpath}", flush=True)
            import vizdoom as vzd
            s.update({
                # post-episode ground truth (log lines are pre-action snapshots)
                "hp": float(game.get_game_variable(vzd.GameVariable.HEALTH)),
                "ammo": float(game.get_game_variable(vzd.GameVariable.AMMO2)),
                "kills": int(game.get_game_variable(vzd.GameVariable.KILLCOUNT)),
                "seed": seed,
            })
            summaries.append(s)
            print(f"episode {ep} done: {s}", flush=True)
            with open(str(path).replace(".jsonl", ".summary.json"), "w") as f:
                json.dump({**s, "scenario": SCENARIO,
                           "brain": "heuristic", "path": str(path)}, f,
                          indent=1)
    finally:
        game.close()

    print("SUMMARY:", json.dumps(summaries, indent=1))


if __name__ == "__main__":
    main()
