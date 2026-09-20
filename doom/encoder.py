"""Game state -> Jev JSON snapshot.

Uses objects_info (world coords, velocity, visibility) + ANGLE game variable.
No pixels touch Jev: everything numeric/textual, with units documented in
the question instructions (bearings in degrees, right-positive).
"""
import math

import numpy as np

from . import config as C

MAX_ENEMIES = 8


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


def encode(state, game_vars, last: dict | None = None,
           focus: dict | None = None) -> dict:
    """state: vizdoom GameState, game_vars: [health, ammo, kills, angle?].

    last: feedback from the previous decision
    {"action": str, "hp_change": float, "ammo_used": float, "kills_change": int}
    so Jev can see the consequences of its last pick.

    focus: current engagement-lock target
    {"id": int, "type": str, "bearing": float, "engaged": int}
    (last-seen bearing while the target is off-screen). None when no lock.
    """
    health, ammo, kills = (float(game_vars[i]) for i in range(3))
    angle = float(game_vars[3]) if len(game_vars) > 3 else 0.0
    # Issue-3: appended by doom_env (HITS_TAKEN, DAMAGECOUNT). Length-guarded
    # so older recordings / builds without them still decode.
    hits_taken = float(game_vars[4]) if len(game_vars) > 4 else None
    damage = float(game_vars[5]) if len(game_vars) > 5 else None

    px, py = 0.0, 0.0
    for o in state.objects or []:
        if o.name == "DoomPlayer":
            px, py = float(o.position_x), float(o.position_y)
            break

    enemies = []
    visible_ids = {label.object_id for label in (state.labels or [])}
    categories = {label.object_id: getattr(label, "object_category", "")
                  for label in (state.labels or [])}
    labels_by_id = {label.object_id: label for label in (state.labels or [])}
    # Screen width for the label x-error: derive from the frame when
    # available (320 wide -> center 160px), else assume 320.
    screen_w = 320
    sb = getattr(state, "screen_buffer", None)
    if sb is not None and getattr(sb, "ndim", 0) == 3:
        screen_w = int(sb.shape[2] if sb.shape[0] <= 4 else sb.shape[1])
    elif sb is not None and getattr(sb, "ndim", 0) == 2:
        screen_w = int(sb.shape[1])
    # Skip non-threats: the player, impact effects, and VISIBLE pickups
    # (labels carry clean categories: Monster vs Armor/Weapon/...).
    # Off-screen pickups can't be categorized; they only add turn bias.
    IGNORE = {"DoomPlayer", "BulletPuff", "Blood"}
    for o in state.objects or []:
        if o.name in IGNORE:
            continue
        if o.id in visible_ids and categories.get(o.id) != "Monster":
            continue
        dx = float(o.position_x) - px
        dy = float(o.position_y) - py
        dist = math.hypot(dx, dy) or 1.0
        abs_deg = math.degrees(math.atan2(dy, dx))
        # Doom: facing +X at angle 0, right hand points -Y, so screen-right
        # is negative atan2 direction -> bearing = angle - abs (right positive)
        bearing = _norm180(angle - abs_deg)
        # Radial velocity: negative = closing in on the player.
        vx, vy = float(o.velocity_x), float(o.velocity_y)
        closing = (vx * dx + vy * dy) / dist < -1.0
        # Screen-x centering error (pixels, + = right of center) from the
        # label box, so Jev can micro-adjust. Off-screen: None.
        x_err = None
        lb = labels_by_id.get(o.id)
        if o.id in visible_ids and lb is not None:
            x_err = round(float(lb.x) + float(lb.width) / 2 - screen_w / 2)
        enemies.append({
            "id": o.id,
            "type": o.name,
            "bearing": round(bearing, 1),
            "dist": round(dist),
            "range": _bucket(dist),
            "visible": o.id in visible_ids,
            "closing": closing,
            "x_err": x_err,
        })

    # Most threatening first: visible, then closest. Cap for token budget.
    enemies.sort(key=lambda e: (not e["visible"], e["dist"]))
    enemies = enemies[:MAX_ENEMIES]

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

    snap = {
        "player": {"health": health, "ammo": int(ammo), "kills": int(kills),
                   "angle": round(angle, 1), "pos": [round(px), round(py)]},
        "enemies": enemies,
        "sectors": sectors,
        "center_visible": center_visible,
        "focus": focus,  # engagement lock (None when no target held)
    }
    if hits_taken is not None:
        snap["player"]["hits_taken"] = int(hits_taken)
    if damage is not None:
        snap["player"]["damage"] = int(damage)
    if last is not None:
        snap["last"] = last

    # Corridor maps: per-sector wall proximity from the depth buffer.
    # "wall" = blocked that way, "open" = free path. Robust median
    # (enemies are a minority of pixels).
    if getattr(state, "depth_buffer", None) is not None:
        d = state.depth_buffer
        w = d.shape[1]
        thirds = {"left": d[:, :w // 3], "center": d[:, w // 3:2 * w // 3],
                  "right": d[:, 2 * w // 3:]}
        snap["path"] = {
            name: ("open" if float(np.median(slab)) >= 20.0 else "wall")
            for name, slab in thirds.items()
        }
    return snap
