"""Game state -> Jev JSON snapshot.

Uses objects_info (world coords + visibility) + ANGLE game variable.
No pixels touch Jev: bearings in degrees, distances bucketed.
"""
import math

from . import config as C


def _norm180(deg: float) -> float:
    while deg > 180:
        deg -= 360
    while deg <= -180:
        deg += 360
    return deg


def _bucket(dist: float) -> str:
    if dist < C.CLOSE_DIST:
        return "close"
    if dist < C.MID_DIST:
        return "mid"
    return "far"


def encode(state, game_vars) -> dict:
    """state: vizdoom GameState, game_vars: list [health, ammo, kills, angle?]."""
    health, ammo, kills = (float(game_vars[i]) for i in range(3))
    angle = float(game_vars[3]) if len(game_vars) > 3 else 0.0

    px, py = 0.0, 0.0
    for o in state.objects or []:
        if o.name == "DoomPlayer":
            px, py = float(o.position_x), float(o.position_y)
            break

    enemies = []
    visible_ids = {label.object_id for label in (state.labels or [])}
    # Skip non-threats: the player, bullet impact puffs, blood.
    IGNORE = {"DoomPlayer", "BulletPuff", "Blood"}
    for o in state.objects or []:
        if o.name in IGNORE:
            continue
        dx = float(o.position_x) - px
        dy = float(o.position_y) - py
        dist = math.hypot(dx, dy)
        abs_deg = math.degrees(math.atan2(dy, dx))
        # Doom: facing +X at angle 0, right hand points -Y, so screen-right
        # is negative atan2 direction -> bearing = angle - abs (right positive)
        bearing = _norm180(angle - abs_deg)
        enemies.append({
            "bearing": round(bearing, 1),
            "dist": round(dist, 1),
            "range": _bucket(dist),
            "visible": o.id in visible_ids,
        })

    sectors = {}
    for name, lo, hi in (("left", -180, -C.CENTER_DEGREES),
                         ("center", -C.CENTER_DEGREES, C.CENTER_DEGREES),
                         ("right", C.CENTER_DEGREES, 180)):
        in_sector = [e for e in enemies if lo <= e["bearing"] < hi or
                     (name == "right" and e["bearing"] == 180)]
        if not in_sector:
            sectors[name] = {"enemies": 0, "nearest": "-", "visible": 0}
        else:
            nearest = min(in_sector, key=lambda e: e["dist"])
            sectors[name] = {
                "enemies": len(in_sector),
                "nearest": nearest["range"],
                "visible": sum(1 for e in in_sector if e["visible"]),
            }

    center_visible = any(
        e["visible"] and abs(e["bearing"]) <= C.CENTER_DEGREES for e in enemies
    )

    return {
        "player": {"health": health, "ammo": ammo, "kills": int(kills)},
        "enemies_total": len(enemies),
        "sectors": sectors,
        "center_visible": center_visible,
    }
