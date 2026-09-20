"""Jev policy: snapshot -> answers -> button vector."""
import math
import time

import httpx

from . import config as C


def target_ids(snapshot: dict) -> list[str]:
    """Choice keys for the dynamic target question (enemy idx + none)."""
    return [str(e["idx"]) for e in snapshot.get("enemies", [])] + ["none"]


def validate_choice(answer, ids: list[str]):
    """ultrafast-style fail-closed check: the answer when it is a
    well-formed Choice over exactly `ids`, else None. Never raises.

    probabilities are optional (older shapes), but when present they must
    cover ids exactly, be finite in [0,1], sum to ~1 and agree with choice.
    """
    try:
        choice = answer["choice"]
        conf = answer["confidence"]
        if choice not in ids or not isinstance(conf, (int, float)):
            return None
        if not math.isfinite(conf) or not 0 <= conf <= 1:
            return None
        probs = answer.get("probabilities")
        if probs is not None:
            if set(probs) != set(ids):
                return None
            vals = list(probs.values())
            if not all(isinstance(v, (int, float)) and math.isfinite(v)
                       and 0 <= v <= 1 for v in vals):
                return None
            if abs(sum(vals) - 1) >= 0.02:
                return None
            if probs[choice] < max(vals) - 1e-6:
                return None
        return answer
    except (KeyError, TypeError, ValueError):
        return None


def picked_target(answers: dict, snapshot: dict) -> dict | None:
    """Enemy dict Jev picked in the target question, or None (none/invalid).

    Validation is fail-closed: an out-of-range idx is treated like none.
    """
    ans = validate_choice((answers or {}).get("target"), target_ids(snapshot))
    if ans is None or ans["choice"] == "none":
        return None
    idx = int(ans["choice"])
    return next((e for e in snapshot.get("enemies", []) if e["idx"] == idx),
                None)


def _turn_tics_for(bearing: float) -> int:
    """Bearing-proportional hold, clamped to [TURN_TICS_MIN, TURN_TICS_MAX]."""
    return min(C.TURN_TICS_MAX,
               max(C.TURN_TICS_MIN, round(abs(bearing) / C.TURN_DEG_PER_TIC)))


def _target_question(snapshot: dict) -> dict:
    criteria = {
        str(e["idx"]): {"type": e["type"], "side": e["side"],
                        "range": e["range"], "visible": e["visible"],
                        "closing": e["closing"]}
        for e in snapshot.get("enemies", [])
    }
    criteria["none"] = "No enemy listed / nothing worth engaging"
    return {"type": "choice",
            "instructions": {"goal": C.TARGET_GOAL, "rules": C.RULES_COMMON},
            "criteria": criteria}


def build_questions(snapshot: dict, scenario: str = "defend") -> dict:
    """Questions for ONE request, built per snapshot (indexed action space).

    defend: target (dynamic, keyed by enemy idx) + fire + danger.
    """
    ammo = int(snapshot["player"]["ammo"])
    danger = {"type": "score",
              "instructions": {"goal": C.DANGER_GOAL, "rules": C.RULES_COMMON},
              "criteria": C.QUESTIONS["danger"]["criteria"]}
    return {
        "target": _target_question(snapshot),
        "fire": {"type": "choice",
                 "instructions": {"goal": C.FIRE_GOAL.format(ammo=ammo),
                                  "rules": C.RULES_COMMON},
                 "criteria": C.QUESTIONS["fire"]["criteria"]},
        "danger": danger,
    }


def decide(client: httpx.Client, snapshot: dict,
           scenario: str = "defend") -> tuple[dict, dict, float]:
    """One system_one call. Returns (answers, usage, latency_ms)."""
    body = {"model": C.MODEL, "state": snapshot,
            "questions": build_questions(snapshot, scenario)}
    t0 = time.time()
    r = client.post(
        C.API_URL,
        headers={"Authorization": f"Bearer {C.API_KEY}"},
        json=body,
    )
    r.raise_for_status()
    data = r.json()
    return data["answers"], data.get("usage", {}), (time.time() - t0) * 1000


#: Issue-15 scan state: next low-confidence defend turn. Alternates
#: L,R,L,R... module-global (4-tic holds).
_scan_turn = [0, 1, 0]


#: Threat memory: sector ("left"/"center"/"right") that most
#: recently held a visible enemy. Updated every decision from the snapshot.
_last_seen: str | None = None
#: Damage tracking: previous decision's HITS_TAKEN counter.
_prev_hits: float | None = None

#: Issue-17 turn balance: cumulative defend turn counts (kept for the
#: scoreboard; strict scan alternation below is inherently balanced).
_turn_left_n = 0
_turn_right_n = 0

#: Issue-21 sustained-fire streak: consecutive defend decisions that fired
#: on a centered+visible, non-far target. Drives chained-burst holds.
#: Reset by any non-chained defend decision and every reset_episode().
_fire_streak = 0


def reset_episode() -> None:
    global _last_seen, _prev_hits
    global _turn_left_n, _turn_right_n
    global _scan_turn, _fire_streak
    _last_seen = None
    _prev_hits = None
    _turn_left_n = 0
    _turn_right_n = 0
    _scan_turn = [0, 1, 0]
    _fire_streak = 0


def _visible_count(snapshot: dict) -> int:
    return sum(1 for e in snapshot.get("enemies", []) if e["visible"])


def _sense(snapshot: dict) -> tuple[bool, int]:
    """Update threat memory; return (took_damage, n_visible).

    took_damage is true when the HITS_TAKEN counter rose since the last
    decision, falling back to last.hp_change < 0 on builds without the
    counter. _last_seen tracks the sector of the nearest visible enemy.
    """
    global _last_seen, _prev_hits
    nearest = None
    for e in snapshot.get("enemies", []):
        if e["visible"] and (nearest is None or e["dist"] < nearest["dist"]):
            nearest = e
    if nearest is not None:
        if nearest["bearing"] < -C.CENTER_DEGREES:
            _last_seen = "left"
        elif nearest["bearing"] > C.CENTER_DEGREES:
            _last_seen = "right"
        else:
            _last_seen = "center"
    hits = snapshot["player"].get("hits_taken")
    if hits is not None and _prev_hits is not None:
        took = hits > _prev_hits
    else:
        took = snapshot.get("last", {}).get("hp_change", 0) < 0
    _prev_hits = hits if hits is not None else _prev_hits
    return took, _visible_count(snapshot)


def _note_turn(vec: list) -> None:
    """Issue-17: record a defend turn for balance accounting."""
    global _turn_left_n, _turn_right_n
    if vec == [1, 0, 0]:
        _turn_left_n += 1
    elif vec == [0, 1, 0]:
        _turn_right_n += 1


def _chainable_target(snapshot: dict, target: dict | None) -> bool:
    """Issue-21: may a chained burst fire at this snapshot?

    Requires a centered+visible enemy at close/mid range — the picked
    target when there is one, else any. Far-range never chains.
    """
    pool = [target] if target is not None else snapshot.get("enemies", [])
    near = min(
        (e["dist"] for e in pool
         if e.get("visible") and abs(e.get("bearing", 999)) <= C.CENTER_DEGREES),
        default=None,
    )
    return near is not None and near < C.MID_DIST


def to_action(answers: dict, snapshot: dict,
              last_turn: list | None = None,
              scenario: str = "defend",
              after_turn: bool = False) -> tuple[list, int, str]:
    """Map answers to ([left, right, attack], hold_tics, reason).

    last_turn: unused (kept for call compatibility; low-conf now scans).
    after_turn: unused (kept for call compatibility).
    """
    global _fire_streak
    ammo = snapshot["player"]["ammo"]
    target = picked_target(answers, snapshot)
    fire_ans = answers.get("fire", {})
    # Issue-16: fire is a relative Choice {shoot, hold} carrying the
    # numeric rule (side = centered AND visible -> shoot). Fire iff the
    # model picks shoot: no Noul threshold, no center_visible backstop
    # (trusting the model is the experiment). Ammo gate stays.
    # Legacy Noul shape ({"noul": p}) still fires via the old threshold
    # so mid-rollout mixed answers fail safe instead of going silent.
    if "choice" in fire_ans:
        fire_choice = fire_ans["choice"]
        fire_conf = fire_ans.get("confidence")
        attack = ammo > 0 and fire_choice == "shoot"
        reason = (f"fire choice={fire_choice}"
                  + (f" conf={fire_conf:.2f}"
                     if isinstance(fire_conf, (int, float)) else ""))
    else:
        fire_p = fire_ans.get("noul", 0.0)
        attack = (
            ammo > 0
            and fire_p >= C.FIRE_THRESHOLD
            and snapshot["center_visible"]
        )
        reason = f"fire p={fire_p:.2f} (legacy noul)"
    if attack:
        _sense(snapshot)  # keep threat memory fresh even while firing
        if _chainable_target(snapshot, target):
            # Issue-21 sustained fire: target is STILL centered+visible at
            # close/mid range, so chain the burst — longer holds on
            # consecutive shoot picks, hard-capped so one hold can never
            # freeze the bot. Under heavy fire (danger high) fire single
            # bursts only: the next decision must come fast.
            _fire_streak += 1
            danger = answers.get("danger", {}).get("score", 0.0)
            if danger >= C.BURST_DANGER_HI:
                tics = C.FIRE_TICS
            else:
                tics = min(C.FIRE_TICS
                            + C.BURST_STEP_TICS * (_fire_streak - 1),
                            C.BURST_MAX_TICS)
            return [0, 0, 1], tics, (
                f"{reason} burst x{_fire_streak} tics={tics} "
                f"danger={danger:.2f}")
        # Firing blind (no centered+visible target) or at far range:
        # single shot only, chain broken — never spray blind.
        _fire_streak = 0
        return [0, 0, 1], C.FIRE_TICS, reason
    # Not firing: any chain ends here (bursts need consecutive shoots).
    _fire_streak = 0

    # Issue-3: hit from off-screen with no strafe buttons here -> turn to
    # face the most recently visible threat sector.
    took_damage, _ = _sense(snapshot)
    if (took_damage and not snapshot["center_visible"]
            and _last_seen in ("left", "right")):
        vec = [1, 0, 0] if _last_seen == "left" else [0, 1, 0]
        _note_turn(vec)
        return vec, C.TURN_TICS, f"face threat {_last_seen} (damage, none visible)"
    _ = after_turn

    # Indexed target (ultrafast-style): Jev picked an enemy by idx; code
    # resolves the geometry — bearing-proportional turn toward it, 4-tic
    # cap so the bot re-aims every decision. Off-screen targets carry
    # bearings too (objects_info), so no blind sweep is needed.
    if target is not None:
        b = target["bearing"]
        if abs(b) <= C.CENTER_DEGREES:
            return [0, 0, 0], C.TURN_TICS, f"target {target['idx']} centered b={b}"
        vec = [1, 0, 0] if b < 0 else [0, 1, 0]
        _note_turn(vec)
        return vec, _turn_tics_for(b), (
            f"target {target['idx']} {target['side']} b={b}")

    # none / invalid: alternating scan turns instead of freezing.
    global _scan_turn
    _scan_turn = ([0, 1, 0] if _scan_turn == [1, 0, 0] else [1, 0, 0])
    side = "left" if _scan_turn == [1, 0, 0] else "right"
    _note_turn(_scan_turn)
    return _scan_turn, C.TURN_TICS, f"scan {side} (no target)"
