"""Game state -> Jev JSON snapshot.

Uses objects_info (world coords, velocity, visibility) + ANGLE game variable.
No pixels touch Jev: everything numeric/textual, with units documented in
the question instructions (bearings in degrees, right-positive).
"""
import math

import numpy as np

from . import config as C

MAX_ENEMIES = 8

# Pickup classification. Label categories first (case-insensitive);
# object-name fallback for off-screen items (no label attached).
# deadly_corridor has one static pickup (armor bonus at the far end);
# shotguns/chainguns/clips appear mid-episode as monster drops.
PICKUP_CATEGORIES = {"weapon": "weapon", "ammo": "ammo",
                     "health": "health", "armor": "armor"}

PICKUP_NAMES = {
    "shotgun": "weapon", "supershotgun": "weapon", "chaingun": "weapon",
    "rocketlauncher": "weapon", "plasmarifle": "weapon", "bfg9000": "weapon",
    "chainsaw": "weapon",
    "clip": "ammo", "clipbox": "ammo", "shell": "ammo", "shellbox": "ammo",
    "rocketammo": "ammo", "rocketbox": "ammo", "cell": "ammo",
    "cellpack": "ammo",
    "medikit": "health", "stimpack": "health", "healthbonus": "health",
    "greenarmor": "armor", "bluearmor": "armor", "armorbonus": "armor",
}

PICKUP_KINDS = ("health", "ammo", "weapon", "armor")


def _norm180(deg: float) -> float:
    while deg > 180:
        deg -= 360
    while deg <= -180:
        deg += 360
    return deg


def _rel(px: float, py: float, angle: float, o) -> tuple[float, float]:
    """Bearing (deg, right-positive) + distance from player to object."""
    dx = float(o.position_x) - px
    dy = float(o.position_y) - py
    dist = math.hypot(dx, dy) or 1.0
    abs_deg = math.degrees(math.atan2(dy, dx))
    # Doom: facing +X at angle 0, right hand points -Y, so screen-right
    # is negative atan2 direction -> bearing = angle - abs (right positive)
    return _norm180(angle - abs_deg), dist


def _pickup_kind(o, categories: dict) -> str | None:
    cat = (categories.get(o.id) or "").strip().lower()
    if cat in PICKUP_CATEGORIES:
        return PICKUP_CATEGORIES[cat]
    return PICKUP_NAMES.get(o.name.lower())


def _bucket(dist: float) -> str:
    if dist < C.CLOSE_DIST:
        return "close"
    if dist < C.MID_DIST:
        return "mid"
    return "far"


def encode(state, game_vars, last: dict | None = None) -> dict:
    """state: vizdoom GameState, game_vars: [health, ammo, kills, angle?,
    selected_weapon?, selected_weapon_ammo?, shotgun_owned?, shells?].

    last: feedback from the previous decision
    {"action": str, "hp_change": float, "ammo_used": float, "kills_change": int}
    so Jev can see the consequences of its last pick.
    """
    health, ammo, kills = (float(game_vars[i]) for i in range(3))
    angle = float(game_vars[3]) if len(game_vars) > 3 else 0.0
    # Corridor-only weapon vars (doom_env appends them after ANGLE);
    # -1/0 defaults on scenarios that don't expose them.
    selected = int(game_vars[4]) if len(game_vars) > 4 else -1
    selected_ammo = int(game_vars[5]) if len(game_vars) > 5 else -1
    shotgun_owned = bool(game_vars[6]) if len(game_vars) > 6 else False
    shells = int(game_vars[7]) if len(game_vars) > 7 else 0

    px, py = 0.0, 0.0
    for o in state.objects or []:
        if o.name == "DoomPlayer":
            px, py = float(o.position_x), float(o.position_y)
            break

    enemies = []
    pickups: dict[str, list] = {k: [] for k in PICKUP_KINDS}
    visible_ids = {label.object_id for label in (state.labels or [])}
    categories = {label.object_id: getattr(label, "object_category", "")
                  for label in (state.labels or [])}
    # Skip non-threats: the player, impact effects, and pickups
    # (labels carry clean categories: Monster vs Weapon/Ammo/...).
    # Off-screen pickups can't be categorized by label, so fall back
    # to object names; either way they leave the enemy list.
    IGNORE = {"DoomPlayer", "BulletPuff", "Blood"}
    for o in state.objects or []:
        if o.name in IGNORE:
            continue
        kind = _pickup_kind(o, categories)
        bearing, dist = _rel(px, py, angle, o)
        if kind is not None:
            pickups[kind].append({
                "kind": kind,
                "name": o.name,
                "bearing": round(bearing, 1),
                "dist": round(dist),
                "visible": o.id in visible_ids,
            })
            continue
        # Radial velocity: negative = closing in on the player.
        dx = float(o.position_x) - px
        dy = float(o.position_y) - py
        vx, vy = float(o.velocity_x), float(o.velocity_y)
        closing = (vx * dx + vy * dy) / dist < -1.0
        enemies.append({
            "type": o.name,
            "bearing": round(bearing, 1),
            "dist": round(dist),
            "range": _bucket(dist),
            "visible": o.id in visible_ids,
            "closing": closing,
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

    # Nearest pickup per kind (None when absent): {kind, bearing, dist, visible}.
    nearest = {}
    for kind, items in pickups.items():
        items.sort(key=lambda p: (not p["visible"], p["dist"]))
        nearest[kind] = items[0] if items else None

    snap = {
        "player": {"health": health, "ammo": int(ammo), "kills": int(kills),
                    "angle": round(angle, 1), "pos": [round(px), round(py)],
                    "selected_weapon": selected,
                    "selected_weapon_ammo": selected_ammo,
                    "shotgun_owned": shotgun_owned, "shells": shells},
        "enemies": enemies,
        "pickups": nearest,
        "sectors": sectors,
        "center_visible": center_visible,
    }
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
