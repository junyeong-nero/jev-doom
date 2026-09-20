"""Episode loop: encode -> Jev -> act, with JSONL logging.

1-flight async: the game loop never blocks on the API. While the next
decide() runs in a background thread, the current action keeps being
applied; the fresh action swaps in on arrival.

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

# Fixed seed suite for comparable evaluation (issue #6). Same seeds +
# same code => same spawn trajectory; Jev answers may still vary
# (server-side sampling), so report mean/std, not single runs.
SEED_SUITE = [1, 2, 3, 4, 5]


def _fire_log_fields(answers: dict) -> dict:
    """Defensive fire logging for both answer shapes (issue #16).

    New Choice shape {"choice": "shoot"/"hold", "confidence": ...} ->
    {"fire": 1.0/0.0, "fire_choice": ..., "fire_conf": ...}.
    Old Noul shape {"noul": p} -> {"fire": p} (no fire_choice key),
    so old and new logs stay readable by the same tools.
    """
    fire_ans = answers.get("fire", {})
    if "choice" in fire_ans:
        choice = fire_ans.get("choice")
        conf = fire_ans.get("confidence")
        return {
            "fire": 1.0 if choice == "shoot" else 0.0,
            "fire_choice": choice,
            "fire_conf": (round(conf, 3)
                          if isinstance(conf, (int, float)) else conf),
        }
    return {"fire": round(fire_ans.get("noul", 0.0), 3)}


def _fire_str(answers: dict) -> str:
    """One-line fire display handling both Choice and legacy Noul answers."""
    fire_ans = answers.get("fire", {})
    if "choice" in fire_ans:
        return f"fire={fire_ans.get('choice')}"
    return f"fire={fire_ans.get('noul', 0.0):.2f}"


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


#: Heuristic baseline (mirrors tirukovelamanoj/jev-plays-doom rule brain):
#: nearest-by-distance target, fire within ±8°, else turn/strafe toward it.
#: Ours: negative bearing = LEFT. 3-button scenarios only (no API calls).
HEURISTIC_FIRE_DEG = 8
HEURISTIC_TURN_TICS = 4  # their cadence: fast re-aim, no settling


def heuristic_action(snapshot: dict, scenario: str) -> tuple[list, int, str]:
    enemies = snapshot.get("enemies", [])
    vis = [e for e in enemies if e.get("visible")]
    pool = vis or enemies  # aim at off-screen bearings too (they do)
    if not pool:
        return [0, 0, 0], C.TURN_TICS, "heuristic: no target"
    tgt = min(pool, key=lambda e: e["dist"])  # nearest by DISTANCE (anti-spin)
    b = tgt["bearing"]
    if abs(b) <= HEURISTIC_FIRE_DEG:
        return [0, 0, 1], C.FIRE_TICS, f"heuristic fire b={b}"
    vec = [1, 0, 0] if b < 0 else [0, 1, 0]  # left / right (or strafe)
    return vec, HEURISTIC_TURN_TICS, f"heuristic turn b={b}"


def run_heuristic_episode(game, log, scenario: str = "defend",
                          frames: list | None = None,
                          seed: int | None = None) -> dict:
    """No-API baseline with the same log schema as the Jev loop."""
    if seed is not None:
        game.set_seed(seed)
    game.new_episode()
    reset_episode()
    decisions, shots = 0, 0
    atk_idx = C.ATTACK_IDX.get(scenario, 2)
    while not game.is_episode_finished():
        state = game.get_state()
        if state is None:
            break
        if frames is not None and state.screen_buffer is not None:
            frames.append(state.screen_buffer.transpose(1, 2, 0).copy())
        snapshot = encode(state, list(state.game_variables))
        action, tics, reason = heuristic_action(snapshot, scenario)
        if action[atk_idx]:
            shots += 1
            _step(game, action, tics, frames)
            _step(game, [0] * len(action), C.RELEASE_TICS, frames)
        else:
            _step(game, action, tics, frames)
        decisions += 1
        fired = bool(action[atk_idx])
        aim = ("center" if fired else
               "left" if action[0] else "right" if action[1] else "center")
        vis_close = any(e.get("visible") and e.get("range") == "close"
                        for e in snapshot.get("enemies", []))
        vis_any = any(e.get("visible") for e in snapshot.get("enemies", []))
        log({
            "aim": aim, "aim_conf": 1.0,
            "fire": 1.0 if fired else 0.0,
            "danger": 2.0 if vis_close else 1.0 if vis_any else 0.0,
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
    atk_idx = C.ATTACK_IDX.get(scenario, 2)
    n_btn = len(game.get_available_buttons())
    cur = [0] * n_btn  # last safe action; warn-hold zeros until d1 arrives
    cycle = False  # re-press semi-auto fire while holding an attack action
    engaged = {}  # object id -> {type, last bearing/dist, engaged count}
    focus = None  # lock record: {id, type, bearing, dist, engaged, misses}
    focus_prev_kills = None  # focus baseline (separate from feedback baseline)

    def fresh():
        """Snapshot the live state (+record frame, +focus lock).

        None if the episode is over.
        """
        nonlocal focus, focus_prev_kills
        state = game.get_state()
        if state is None:
            return None
        if frames is not None and state.screen_buffer is not None:
            frames.append(state.screen_buffer.transpose(1, 2, 0).copy())
        snap = encode(state, list(state.game_variables), last=prev)
        focus = _update_focus(engaged, focus, snap["enemies"],
                              snap["player"]["kills"], focus_prev_kills)
        focus_prev_kills = snap["player"]["kills"]
        snap["focus"] = _public_focus(focus)
        return snap

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
            focus_id = (snapshot.get("focus") or {}).get("id")
            action, tics, reason = to_action(answers, snapshot, last_turn,
                                            scenario, after_turn=after_turn)
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
                    **_fire_log_fields(answers),
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
                          f"{_fire_str(answers)} "
                          f"danger={answers['danger']['score']:.2f} "
                          f"hp={snapshot['player']['health']:.0f} "
                          f"ammo={snapshot['player']['ammo']:.0f} "
                          f"kills={snapshot['player']['kills']} "
                          f"focus={focus_id} "
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
    ap.add_argument("--seed", type=int, default=None,
                    help="vizdoom RNG seed (set before new_episode). "
                         "With --episodes N, episode ep uses seed+N.")
    ap.add_argument("--suite", action="store_true",
                    help=f"run the fixed seed suite {SEED_SUITE} "
                         "(overrides --episodes/--seed)")
    ap.add_argument("--brain", choices=["jev", "heuristic"], default="jev",
                    help="jev: TypeSafe API policy; heuristic: no-API "
                         "geometry baseline (defend/basic/simple only)")
    args = ap.parse_args()

    if args.brain == "heuristic" and args.scenario == "corridor":
        sys.exit("heuristic brain supports defend/basic/simple only "
                 "(3-button layouts)")

    if args.brain == "jev" and not C.API_KEY:
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
                btag = "_heuristic" if args.brain == "heuristic" else ""
                path = run_dir / f"{ts}_{args.scenario}_ep{ep}{suffix}{btag}.jsonl"
                print(f"episode {ep} (seed={seed}, brain={args.brain}) -> {path}",
                      flush=True)
                frames = [] if args.record else None
                with open(path, "w") as f:
                    def log(obj, f=f):
                        f.write(json.dumps(obj) + "\n")
                    if args.brain == "heuristic":
                        s = run_heuristic_episode(game, log, args.scenario,
                                                  frames, seed=seed)
                    else:
                        s = run_episode(game, client, log, args.scenario,
                                        frames, seed=seed)
                if frames:
                    import numpy as np
                    fpath = (run_dir /
                             f"{ts}_{args.scenario}_ep{ep}{suffix}{btag}_frames.npz")
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
                               "brain": args.brain, "path": str(path)}, f,
                              indent=1)
    finally:
        game.close()

    print("SUMMARY:", json.dumps(summaries, indent=1))


if __name__ == "__main__":
    main()
