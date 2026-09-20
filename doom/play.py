"""Episode loop: encode -> Jev -> act, with JSONL logging.

Usage:
    uv run python -m doom.play --scenario defend --episodes 2
    uv run python -m doom.play --scenario defend --visible   # watch it play
    uv run python -m doom.play --scenario defend --suite     # fixed seed suite (1..5)
    uv run python -m doom.play --scenario defend --seed 1    # single seeded run
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
from .policy import decide, reset_episode, to_action

# Fixed seed suite for comparable evaluation (issue #6). Same seeds +
# same code => same spawn trajectory; Jev answers may still vary
# (server-side sampling), so report mean/std, not single runs.
SEED_SUITE = [1, 2, 3, 4, 5]


def _step(game, action: list, tics: int, frames: list | None) -> None:
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


#: Focus coast: decisions a lost (unseen, no kill credit) target is held
#: via its last-seen bearing before it counts as fully lost.
FOCUS_MAX_MISSES = 3


def _public_focus(focus: dict | None) -> dict | None:
    """Scenario-agnostic lock view for the snapshot (no private counters)."""
    if focus is None:
        return None
    return {"id": focus["id"], "type": focus["type"],
            "bearing": focus["bearing"], "engaged": focus["engaged"]}


def _update_focus(engaged: dict, focus: dict | None, enemies: list,
                  kills_now: int, kills_prev: int | None) -> dict | None:
    """Engagement table + target-lock update. Scenario-agnostic.

    engaged: object id -> {type, last bearing/dist, engaged decision count}.
    Returns the lock record (with private misses/dist) or None. The lock is
    held until the target disappears with a kill credit (kills_change
    attribution is approximate) or stays unseen past FOCUS_MAX_MISSES
    (fully lost); otherwise it coasts on its last-seen bearing.
    """
    by_id = {e["id"]: e for e in enemies}
    for e in enemies:
        rec = engaged.get(e["id"])
        if rec is None:
            engaged[e["id"]] = {"type": e["type"], "bearing": e["bearing"],
                                "dist": e["dist"], "engaged": 0}
        else:
            rec.update({"type": e["type"], "bearing": e["bearing"],
                        "dist": e["dist"]})
    kills_change = kills_now - kills_prev if kills_prev is not None else 0
    if focus is not None:
        cur = by_id.get(focus["id"])
        if cur is not None:
            focus.update({"type": cur["type"], "bearing": cur["bearing"],
                          "dist": cur["dist"], "misses": 0})
        elif kills_change > 0:
            focus = None  # dead (kill credit is approximate)
        else:
            focus["misses"] += 1
            if focus["misses"] > FOCUS_MAX_MISSES:
                focus = None  # fully lost
    if focus is None:
        # enemies arrive sorted visible-first, closest-first.
        visible = [e for e in enemies if e.get("visible")]
        if visible:
            pick = visible[0]
            focus = {"id": pick["id"], "type": pick["type"],
                     "bearing": pick["bearing"], "dist": pick["dist"],
                     "engaged": 0, "misses": 0}
    if focus is not None:
        rec = engaged.setdefault(focus["id"], {
            "type": focus["type"], "bearing": focus["bearing"],
            "dist": focus["dist"], "engaged": 0})
        rec["engaged"] += 1
        focus["engaged"] = rec["engaged"]
    return focus


def run_episode(game, client, log, scenario: str = "defend",
                frames: list | None = None, seed: int | None = None) -> dict:
    if seed is not None:
        game.set_seed(seed)
    game.new_episode()
    reset_episode()
    decisions, latencies, shots = 0, [], 0
    last_turn = None
    after_turn = False  # previous action was a turn -> observe once
    prev = None  # feedback for the next snapshot
    engaged = {}  # object id -> {type, last bearing/dist, engaged count}
    focus = None  # lock record: {id, type, bearing, dist, engaged, misses}
    prev_kills = None
    while not game.is_episode_finished():
        state = game.get_state()
        if state is None:
            break
        if frames is not None and state.screen_buffer is not None:
            frames.append(state.screen_buffer.transpose(1, 2, 0).copy())
        vars_now = list(state.game_variables)
        snapshot = encode(state, vars_now, last=prev)
        focus = _update_focus(engaged, focus, snapshot["enemies"],
                              snapshot["player"]["kills"], prev_kills)
        prev_kills = snapshot["player"]["kills"]
        snapshot["focus"] = _public_focus(focus)
        focus_id = focus["id"] if focus is not None else None
        atk_idx = C.ATTACK_IDX.get(scenario, 2)
        try:
            answers, usage, ms = decide(client, snapshot, scenario)
        except Exception as e:  # Jev hiccup -> hold, keep episode alive
            print(f"  [warn] jev error: {e}, holding", flush=True)
            game.make_action([0] * len(game.get_available_buttons()),
                             C.TURN_TICS)
            continue
        action, tics, reason = to_action(answers, snapshot, last_turn,
                                         scenario, after_turn=after_turn)
        if action[atk_idx]:
            shots += 1
            _step(game, action, tics, frames)
            _step(game, [0] * len(action),
                  C.RELEASE_TICS, frames)  # release: re-press per bullet
        else:
            _step(game, action, tics, frames)
        if action in ([1, 0, 0], [0, 1, 0]):
            last_turn = action
        after_turn = action in ([1, 0, 0], [0, 1, 0])
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
                "shells": snapshot["player"].get("shells", 0),
                "shotgun": snapshot["player"].get("shotgun_owned", False),
                "pickups": {k: (v is not None)
                            for k, v in snapshot.get("pickups", {}).items()},
                "focus": focus_id,
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
                "focus": focus_id,
            })
        if decisions == 1 or decisions % 10 == 0:
            if scenario == "corridor":
                print(f"  d{decisions}: pick={answers['action']['choice']} "
                      f"danger={answers['danger']['score']:.2f} "
                      f"hp={snapshot['player']['health']:.0f} "
                      f"ammo={snapshot['player']['ammo']:.0f} "
                      f"kills={snapshot['player']['kills']} "
                      f"focus={focus_id} "
                      f"[{reason}, {ms:.0f}ms]", flush=True)
            else:
                print(f"  d{decisions}: aim={answers['aim']['choice']} "
                      f"fire={answers['fire']['noul']:.2f} "
                      f"danger={answers['danger']['score']:.2f} "
                      f"hp={snapshot['player']['health']:.0f} "
                      f"ammo={snapshot['player']['ammo']:.0f} "
                      f"kills={snapshot['player']['kills']} "
                      f"focus={focus_id} "
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
                    choices=["defend", "basic", "simple", "corridor"])
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

    if not C.API_KEY:
        sys.exit("no API key: set TYPESAFE_API_KEY or JEV_APIKEY in .env")

    run_dir = Path("runs")
    run_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    game = make_game(args.scenario, visible=args.visible, timeout_tics=args.timeout)
    if args.suite:
        seeds: list = list(SEED_SUITE)
    elif args.seed is not None:
        seeds = [args.seed + ep for ep in range(args.episodes)]
    else:
        seeds = [None] * args.episodes
    summaries = []
    try:
        with httpx.Client(timeout=25) as client:
            for ep, seed in enumerate(seeds):
                suffix = f"_seed{seed}" if seed is not None else ""
                path = run_dir / f"{ts}_{args.scenario}_ep{ep}{suffix}.jsonl"
                print(f"episode {ep} (seed={seed}) -> {path}", flush=True)
                frames = [] if args.record else None
                with open(path, "w") as f:
                    def log(obj, f=f):
                        f.write(json.dumps(obj) + "\n")
                    s = run_episode(game, client, log, args.scenario, frames,
                                    seed=seed)
                if frames:
                    import numpy as np
                    fpath = run_dir / f"{ts}_{args.scenario}_ep{ep}{suffix}_frames.npz"
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
                    json.dump({**s, "scenario": args.scenario,
                               "path": str(path)}, f, indent=1)
    finally:
        game.close()

    print("SUMMARY:", json.dumps(summaries, indent=1))


if __name__ == "__main__":
    main()
