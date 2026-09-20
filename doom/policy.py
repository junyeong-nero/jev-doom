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


def build_questions(snapshot: dict, scenario: str) -> dict:
    """Questions for ONE request, built per snapshot (indexed action space).

    defend-family: target (dynamic, keyed by enemy idx) + fire + danger.
    corridor: action (unchanged text, wrapped) + target + danger; the
    target head is consumed only for turn/attack picks (speculative).
    """
    ammo = int(snapshot["player"]["ammo"])
    danger = {"type": "score",
              "instructions": {"goal": C.DANGER_GOAL, "rules": C.RULES_COMMON},
              "criteria": C.QUESTIONS["danger"]["criteria"]}
    if scenario == "corridor":
        act = C.CORRIDOR_QUESTIONS["action"]
        return {
            "action": {"type": "choice",
                       "instructions": {
                           "goal": act["instructions"].format(ammo=ammo),
                           "rules": C.RULES_COMMON},
                       "criteria": act["criteria"]},
            "target": _target_question(snapshot),
            "danger": danger,
        }
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


_dodge_side = "strafe_left"
_corridor_decisions = 0

#: Issue-15 scan state: next low-confidence defend turn. Alternates
#: L,R,L,R... module-global like _dodge_side (4-tic holds).
_scan_turn = [0, 1, 0]


def _close_threat_ahead(snapshot: dict) -> bool:
    """A close-range threat straight ahead (kiting target)?"""
    center = (snapshot.get("sectors") or {}).get("center") or {}
    if center.get("nearest") == "close":
        return True
    return any(
        e.get("range") == "close"
        and abs(e.get("bearing", 999)) <= C.CENTER_DEGREES
        for e in snapshot.get("enemies", []))


def _kite_ok(snapshot: dict) -> bool:
    """Kiting retreat allowed: close threat straight ahead, single step.

    "Behind open" proxy: the opening sprint already cleared the spawn
    wall, and the last action wasn't a retreat (never back up twice in
    a row — walls close in behind, and the depth-buffer path feature
    only sees forward).
    """
    last = (snapshot.get("last") or {}).get("action")
    if last == "retreat":
        return False
    return _close_threat_ahead(snapshot)


#: Issue-3 threat memory: sector ("left"/"center"/"right") that most
#: recently held a visible enemy. Updated every decision from the snapshot.
_last_seen: str | None = None
#: Issue-3 damage tracking: previous decision's HITS_TAKEN counter.
_prev_hits: float | None = None
#: Issue-3 cover state: set when we strafe toward a wall to break LOS.
_cover_active = False
_cover_visible_before = 0
_cover_ttl = 0
COVER_TTL = 4  # decisions a cover move stays "active" for success check

#: Issue-17 turn balance: cumulative defend turn counts (kept for the
#: scoreboard; strict scan alternation below is inherently balanced).
_turn_left_n = 0
_turn_right_n = 0

#: Issue-21 sustained-fire streak: consecutive defend decisions that fired
#: on a centered+visible, non-far target. Drives chained-burst holds.
#: Reset by any non-chained defend decision and every reset_episode().
_fire_streak = 0

#: Opening sprint: first N corridor decisions always advance, to clear
#: the spawn kill-zone before fighting. (Learned from a scripted rush
#: scoring +495 vs -16 dodging in place.)
OPENING_SPRINT = 6


def reset_episode() -> None:
    global _corridor_decisions, _last_seen, _prev_hits
    global _cover_active, _cover_visible_before, _cover_ttl
    global _turn_left_n, _turn_right_n
    global _scan_turn, _fire_streak, _dodge_side
    _corridor_decisions = 0
    _last_seen = None
    _prev_hits = None
    _cover_active = False
    _cover_visible_before = 0
    _cover_ttl = 0
    _turn_left_n = 0
    _turn_right_n = 0
    _scan_turn = [0, 1, 0]
    _fire_streak = 0
    _dodge_side = "strafe_left"


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


def _cover_side(snapshot: dict) -> str | None:
    """Which way to strafe for cover, or None when no wall is near.

    Prefers the lone wall side; with walls on both sides hugs the side
    with fewer visible enemies (breaks the busier sightline first);
    ties alternate like the dodge reflex.
    """
    global _dodge_side
    path = snapshot.get("path", {})
    walls = [s for s in ("left", "right") if path.get(s) == "wall"]
    if not walls:
        return None
    if len(walls) == 1:
        return f"strafe_{walls[0]}"
    sectors = snapshot.get("sectors", {})
    lv = sectors.get("left", {}).get("visible", 0)
    rv = sectors.get("right", {}).get("visible", 0)
    if lv < rv:
        return "strafe_left"
    if rv < lv:
        return "strafe_right"
    _dodge_side = ("strafe_right"
                   if _dodge_side == "strafe_left" else "strafe_left")
    return _dodge_side


def _cover_result(n_visible: int) -> str:
    """Prefix for this decision's reason when a cover move paid off."""
    global _cover_active, _cover_ttl
    prefix = ""
    if _cover_active:
        if n_visible < _cover_visible_before:
            prefix = (f"cover success (visible "
                      f"{_cover_visible_before}->{n_visible}); ")
            _cover_active = False
            _cover_ttl = 0
        else:
            _cover_ttl -= 1
            if _cover_ttl <= 0:
                _cover_active = False
    return prefix


def _corridor_action(answers: dict, snapshot: dict) -> tuple[list, int, str]:
    global _dodge_side, _corridor_decisions
    global _cover_active, _cover_visible_before, _cover_ttl
    _corridor_decisions += 1
    if _corridor_decisions <= OPENING_SPRINT:
        _sense(snapshot)  # warm threat memory / damage baseline for d7+
        vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
        return vec, tics, "opening sprint"
    took_damage, n_visible = _sense(snapshot)
    prefix = _cover_result(n_visible)
    if took_damage and not snapshot["center_visible"]:
        # Hit from off-screen: break LOS toward the nearest wall, else
        # face the most recently seen threat sector. Fallback when even
        # that is unknown: nearest tracked enemy's sector (off-screen
        # bearings persist in objects_info, so the likely shooter).
        side = _cover_side(snapshot)
        if side is not None:
            _cover_active = True
            _cover_visible_before = n_visible
            _cover_ttl = COVER_TTL
            vec, tics, _ = C.CORRIDOR_ACTIONS[side]
            flank = side.split("_")[1]
            return vec, tics, f"{prefix}seek cover {flank} (damage, nothing ahead)"
        sector = _last_seen
        if sector is None:
            near = min(snapshot.get("enemies", []), key=lambda e: e["dist"],
                       default=None)
            if near is not None:
                sector = ("left" if near["bearing"] < -C.CENTER_DEGREES
                          else "right" if near["bearing"] > C.CENTER_DEGREES
                          else "center")
        if sector in ("left", "right"):
            turn = f"turn_{sector}"
            vec, tics, _ = C.CORRIDOR_ACTIONS[turn]
            return vec, tics, f"{prefix}face threat {sector} (damage, none visible)"
    # Pickup code gate: take the bigger gun as soon as it can fire.
    # (Pressing select while already on shotgun is a harmless no-op,
    # so an unknown selected_weapon still switches.)
    p = snapshot["player"]
    if (p.get("shotgun_owned") and p.get("shells", 0) > 0
            and p.get("selected_weapon") != 3):
        vec, tics, _ = C.CORRIDOR_ACTIONS["switch_to_shotgun"]
        return vec, tics, f"{prefix}switch to shotgun"
    pick = answers["action"]
    choice, conf = pick["choice"], pick["confidence"]
    danger = answers["danger"]["score"]

    if choice in ("attack", "strafe_left_fire", "strafe_right_fire"):
        if snapshot["player"]["ammo"] <= 0 or not snapshot["center_visible"]:
            vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
            return vec, tics, prefix + "downgraded fire (no target/ammo)"
        if danger >= C.DODGE_DANGER and choice == "attack":
            # Never stand still trading fire with 6 shotgunners.
            _dodge_side = ("strafe_right"
                           if _dodge_side == "strafe_left" else "strafe_left")
            vec, tics, _ = C.CORRIDOR_ACTIONS[_dodge_side]
            return vec, tics, prefix + f"dodge (attack@{danger:.1f})"
        vec, tics, reason = C.CORRIDOR_ACTIONS[choice]
        return vec, tics, prefix + reason

    # Kiting retreat: single step back from a close frontal threat.
    # Otherwise (no kite target, or would back into a wall twice in a
    # row) sidestep instead — keeps aim on the threat while moving.
    if choice == "retreat" and not _kite_ok(snapshot):
        _dodge_side = ("strafe_right"
                       if _dodge_side == "strafe_left" else "strafe_left")
        vec, tics, _ = C.CORRIDOR_ACTIONS[_dodge_side]
        return vec, tics, f"sidestep (retreat blocked@{danger:.1f})"

    # Survival reflex: under heavy fire, don't stand still.
    if danger >= C.DODGE_DANGER and choice == "advance":
        _dodge_side = ("strafe_right"
                       if _dodge_side == "strafe_left" else "strafe_left")
        vec, tics, _ = C.CORRIDOR_ACTIONS[_dodge_side]
        return vec, tics, prefix + f"dodge ({choice}@{danger:.1f})"

    if choice in C.CORRIDOR_ACTIONS:
        vec, tics, reason = C.CORRIDOR_ACTIONS[choice]
        if conf < C.SWEEP_CONFIDENCE:
            vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
            return vec, tics, prefix + f"advance (low conf {conf:.2f})"
        # Speculative target head: for a turn, resolve the hold from the
        # picked enemy's bearing when it lies on the chosen side.
        target = picked_target(answers, snapshot)
        if choice in ("turn_left", "turn_right") and target is not None:
            b = target["bearing"]
            if (choice == "turn_left") == (b < 0):
                tics = _turn_tics_for(b)
                reason = f"{reason} -> target {target['idx']} b={b}"
        return vec, tics, prefix + reason
    vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
    return vec, tics, prefix + "advance (fallback)"


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
    after_turn: previous action was a turn -> one observation decision
        (anti-overshoot) before turning again.
    """
    if scenario == "corridor":
        return _corridor_action(answers, snapshot)
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
        threshold = C.FIRE_THRESHOLD.get(scenario, 0.65)
        attack = (
            ammo > 0
            and fire_p >= threshold
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
