"""Jev policy: snapshot -> answers -> button vector."""
import time

import httpx

from . import config as C


def _format(questions: dict, snapshot: dict) -> dict:
    """Fill {ammo} placeholders in question instructions."""
    out = {}
    for k, q in questions.items():
        q = dict(q)
        if "{ammo}" in q.get("instructions", ""):
            q["instructions"] = q["instructions"].format(
                ammo=int(snapshot["player"]["ammo"]))
        out[k] = q
    return out


def decide(client: httpx.Client, snapshot: dict,
           scenario: str = "defend") -> tuple[dict, dict, float]:
    """One system_one call. Returns (answers, usage, latency_ms)."""
    base = C.CORRIDOR_QUESTIONS if scenario == "corridor" else C.QUESTIONS
    questions = _format(base, snapshot)
    body = {"model": C.MODEL, "state": snapshot, "questions": questions}
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

#: Opening sprint: first N corridor decisions always advance, to clear
#: the spawn kill-zone before fighting. (Learned from a scripted rush
#: scoring +495 vs -16 dodging in place.)
OPENING_SPRINT = 6


def reset_episode() -> None:
    global _corridor_decisions, _last_seen, _prev_hits
    global _cover_active, _cover_visible_before, _cover_ttl
    global _turn_left_n, _turn_right_n
    global _scan_turn
    _corridor_decisions = 0
    _last_seen = None
    _prev_hits = None
    _cover_active = False
    _cover_visible_before = 0
    _cover_ttl = 0
    _turn_left_n = 0
    _turn_right_n = 0
    _scan_turn = [0, 1, 0]


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
        return vec, tics, prefix + reason
    vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
    return vec, tics, prefix + "advance (fallback)"


def _in_sector(bearing: float, sector: str) -> bool:
    """Mirror encoder.py sector bounds (CENTER_DEGREES edges)."""
    c = C.CENTER_DEGREES
    if sector == "left":
        return -180 <= bearing < -c
    if sector == "right":
        return c <= bearing <= 180
    return -c <= bearing < c


def _turn_tics(snapshot: dict, sector: str) -> tuple[int, float | None]:
    """Bearing-proportional turn hold for the named sector.

    Uses the nearest VISIBLE enemy in that sector; falls back to
    C.TURN_TICS when none is visible. Returns (tics, bearing_or_None).
    """
    cands = [e for e in snapshot.get("enemies", [])
             if e.get("visible") and _in_sector(e.get("bearing", 999), sector)]
    if not cands:
        return C.TURN_TICS, None
    tgt = min(cands, key=lambda e: e.get("dist", 1 << 30))
    tics = max(C.TURN_TICS_MIN,
               round(abs(tgt["bearing"]) / C.TURN_DEG_PER_TIC))
    # Merger cap: a single hold must never freeze the bot (180 deg would be
    # ~409 tics ~ 12 game-seconds of standing still). Re-aim next decision.
    tics = min(tics, C.TURN_TICS_MAX)
    return tics, tgt["bearing"]


def _note_turn(vec: list) -> None:
    """Issue-17: record a defend turn for balance accounting."""
    global _turn_left_n, _turn_right_n
    if vec == [1, 0, 0]:
        _turn_left_n += 1
    elif vec == [0, 1, 0]:
        _turn_right_n += 1


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
    ammo = snapshot["player"]["ammo"]
    aim = answers["aim"]
    fire_ans = answers.get("fire", {})
    # Issue-16: fire is a relative Choice {shoot, hold} carrying the
    # numeric rule (|bearing| <= 10 AND visible -> shoot). Fire iff the
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
        return [0, 0, 1], C.FIRE_TICS, reason

    # Issue-3: hit from off-screen with no strafe buttons here -> turn to
    # face the most recently visible threat sector.
    took_damage, _ = _sense(snapshot)
    if (took_damage and not snapshot["center_visible"]
            and _last_seen in ("left", "right")):
        vec = [1, 0, 0] if _last_seen == "left" else [0, 1, 0]
        _note_turn(vec)
        return vec, C.TURN_TICS, f"face threat {_last_seen} (damage, none visible)"

    # NOTE: no settle-observe after turns (removed): with an explicit numeric
    # fire rule the bot re-aims immediately instead of pausing.
    _ = after_turn

    if aim["confidence"] >= C.SWEEP_CONFIDENCE:
        if aim["choice"] == "left":
            tics, b = _turn_tics(snapshot, "left")
            if b is None:
                # Issue-17: blind sweep -> turn toward remembered threat.
                if _last_seen == "right":
                    tics2, b2 = _turn_tics(snapshot, "right")
                    _note_turn([0, 1, 0])
                    return [0, 1, 0], tics2, (
                        "aim left blind -> face right (memory)"
                        if b2 is None else f"aim left blind -> face right b={b2}")
                _note_turn([1, 0, 0])
                return [1, 0, 0], tics, "aim left (no visible target)"
            _note_turn([1, 0, 0])
            return [1, 0, 0], tics, f"aim left b={b} tics={tics}"
        if aim["choice"] == "right":
            tics, b = _turn_tics(snapshot, "right")
            if b is None:
                # Issue-17: blind sweep -> turn toward remembered threat.
                if _last_seen == "left":
                    tics2, b2 = _turn_tics(snapshot, "left")
                    _note_turn([1, 0, 0])
                    return [1, 0, 0], tics2, (
                        "aim right blind -> face left (memory)"
                        if b2 is None else f"aim right blind -> face left b={b2}")
                _note_turn([0, 1, 0])
                return [0, 1, 0], tics, "aim right (no visible target)"
            _note_turn([0, 1, 0])
            return [0, 1, 0], tics, f"aim right b={b} tics={tics}"
        return [0, 0, 0], C.TURN_TICS, "aim center"

    # low confidence: alternating scan turns instead of freezing
    # (sweep/hold left the bot standing still while taking fire).
    # Strict alternation is inherently balanced; still counted for stats.
    global _scan_turn
    _scan_turn = ([0, 1, 0] if _scan_turn == [1, 0, 0] else [1, 0, 0])
    side = "left" if _scan_turn == [1, 0, 0] else "right"
    _note_turn(_scan_turn)
    return _scan_turn, C.TURN_TICS, f"scan {side} conf={aim['confidence']:.2f}"
