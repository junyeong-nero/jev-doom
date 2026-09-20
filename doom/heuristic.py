"""Corridor heuristic: snapshot -> 9-button vector. No API, no model.

Geometry-only baseline: nearest visible shooter, fire within ±8°,
else turn toward it at 4-tic cadence. Code-side reflexes handle the rest:
opening sprint out of the spawn kill-zone, cover seek when hit from
off-screen, dodge (firing strafes) under heavy fire, shotgun switch
as soon as it can fire.
"""
from . import config as C

#: Opening sprint: first N decisions always advance, to clear the spawn
#: kill-zone before fighting. (Learned from a scripted rush scoring
#: +495 vs -16 dodging in place.)
OPENING_SPRINT = 6

_corridor_decisions = 0
_dodge_side = "strafe_left_fire"

#: Threat memory: sector ("left"/"center"/"right") that most recently
#: held a visible enemy. Updated every decision from the snapshot.
_last_seen: str | None = None
#: Damage tracking: previous decision's HITS_TAKEN counter.
_prev_hits: float | None = None
#: Cover state: set when we strafe toward a wall to break LOS.
_cover_active = False
_cover_visible_before = 0
_cover_ttl = 0
COVER_TTL = 4  # decisions a cover move stays "active" for success check


def reset_episode() -> None:
    global _corridor_decisions, _last_seen, _prev_hits
    global _cover_active, _cover_visible_before, _cover_ttl
    global _dodge_side
    _corridor_decisions = 0
    _last_seen = None
    _prev_hits = None
    _cover_active = False
    _cover_visible_before = 0
    _cover_ttl = 0
    _dodge_side = "strafe_left_fire"


def _visible_count(snapshot: dict) -> int:
    return sum(1 for e in snapshot.get("enemies", []) if e["visible"])


def danger_of(snapshot: dict) -> float:
    """Danger on the 0–2 scale the dodge reflex uses.

    2.0 = a close enemy is visible (kill-zone), 1.0 = something visible,
    0.0 = nothing. Same scale as DODGE_DANGER.
    """
    enemies = snapshot.get("enemies", [])
    if any(e.get("visible") and e.get("range") == "close" for e in enemies):
        return 2.0
    if any(e.get("visible") for e in enemies):
        return 1.0
    return 0.0


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
    _dodge_side = ("strafe_right_fire"
                   if _dodge_side == "strafe_left_fire"
                   else "strafe_left_fire")
    return _dodge_side.replace("_fire", "")


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


def heuristic_action(snapshot: dict) -> tuple[list, int, str]:
    """Geometry baseline: ([9-button vector], hold_tics, reason)."""
    global _dodge_side, _corridor_decisions
    global _cover_active, _cover_visible_before, _cover_ttl
    _corridor_decisions += 1
    if _corridor_decisions <= OPENING_SPRINT:
        _sense(snapshot)  # warm threat memory / damage baseline for d7+
        vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
        return vec, tics, "opening sprint"
    took_damage, n_visible = _sense(snapshot)
    prefix = _cover_result(n_visible)
    danger = danger_of(snapshot)
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
            vec, _, _ = C.CORRIDOR_ACTIONS[turn]
            return vec, C.HEURISTIC_TURN_TICS, (
                f"{prefix}face threat {sector} (damage, none visible)")
    # Take the bigger gun as soon as it can fire. (Pressing select while
    # already on shotgun is a harmless no-op, so an unknown
    # selected_weapon still switches.)
    p = snapshot["player"]
    if (p.get("shotgun_owned") and p.get("shells", 0) > 0
            and p.get("selected_weapon") != 3):
        vec, tics, _ = C.CORRIDOR_ACTIONS["switch_to_shotgun"]
        return vec, tics, f"{prefix}switch to shotgun"
    enemies = snapshot.get("enemies", [])
    vis = [e for e in enemies if e.get("visible")]
    pool = vis or enemies  # aim at off-screen bearings too
    if not pool:
        vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
        return vec, tics, f"{prefix}advance (no target)"
    tgt = min(pool, key=lambda e: e["dist"])  # nearest by DISTANCE (anti-spin)
    b = tgt["bearing"]
    if tgt.get("visible") and abs(b) <= C.HEURISTIC_FIRE_DEG and p["ammo"] > 0:
        if danger >= C.DODGE_DANGER:
            # Never stand still trading fire with 6 shotgunners:
            # return fire while sidestepping instead.
            _dodge_side = ("strafe_right_fire"
                           if _dodge_side == "strafe_left_fire"
                           else "strafe_left_fire")
            vec, tics, _ = C.CORRIDOR_ACTIONS[_dodge_side]
            return vec, tics, f"{prefix}dodge-fire ({_dodge_side}@{danger:.1f})"
        vec, tics, reason = C.CORRIDOR_ACTIONS["attack"]
        return vec, tics, prefix + reason
    # Aim: turn toward the threat's bearing in short holds so the next
    # decision re-aims instead of freezing mid-turn.
    turn = "turn_left" if b < 0 else "turn_right"
    vec, _, _ = C.CORRIDOR_ACTIONS[turn]
    return vec, C.HEURISTIC_TURN_TICS, f"{prefix}heuristic turn b={b}"
