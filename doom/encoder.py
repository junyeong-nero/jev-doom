"""Game state -> snapshot dict.

Uses objects_info (world coords, velocity, visibility) + ANGLE game variable.
No pixels touch the brain: everything numeric/textual, with units documented
in code (bearings in degrees, right-positive).
"""
import math

from . import config as C

MAX_ENEMIES = 8


def _norm180(deg: float) -> float:
    while deg > 180:
        deg -= 360
    while deg <= -180:
        deg += 360
    return deg


def _rel(px: float, py: float, angle: float, o,
         tics: float = 0.0) -> tuple[float, float]:
    """Bearing (deg, right-positive) + distance from player to object.

    tics > 0 dead-reckons the object along its velocity first (latency lead).
    """
    dx = float(o.position_x) + float(o.velocity_x) * tics - px
    dy = float(o.position_y) + float(o.velocity_y) * tics - py
    dist = math.hypot(dx, dy) or 1.0
    abs_deg = math.degrees(math.atan2(dy, dx))
    # Doom: facing +X at angle 0, right hand points -Y, so screen-right
    # is negative atan2 direction -> bearing = angle - abs (right positive)
    return _norm180(angle - abs_deg), dist


def _player(state) -> tuple[float, float]:
    for o in state.objects or []:
        if o.name == "DoomPlayer":
            return float(o.position_x), float(o.position_y)
    return 0.0, 0.0


def bearing_to(state, game_vars, object_id: int):
    """(bearing, dist, visible) of a live object, or None when absent.

    Cheap per-tic read for the latency-gap tracker: no snapshot build.
    """
    angle = float(game_vars[3]) if len(game_vars) > 3 else 0.0
    px, py = _player(state)
    for o in state.objects or []:
        if o.id == object_id and o.name != "DoomPlayer":
            b, d = _rel(px, py, angle, o)
            vis = any(lb.object_id == object_id for lb in (state.labels or []))
            return round(b, 1), d, vis
    return None


def _lead_angle(angle: float, lead: dict | None) -> float:
    """Predicted facing when the answer lands (Flappy-style latency lead).

    turn=+1 is TURN_RIGHT, which LOWERS Doom's angle (bearing = angle - abs).
    cap_deg bounds the sweep (the tracker stops turning once centered).
    """
    if not lead or not lead.get("turn"):
        return angle
    delta = C.TURN_DEG_PER_TIC * float(lead["tics"])
    cap = lead.get("cap_deg")
    if cap is not None:
        delta = min(delta, float(cap))
    return angle - delta * (1 if lead["turn"] > 0 else -1)


def _bucket(dist: float) -> str:
    if dist < C.CLOSE_DIST:
        return "close"
    if dist < C.MID_DIST:
        return "mid"
    return "far"


SIDE_WIDE_DEGREES = 45.0  # |bearing| beyond this is far_left / far_right


def side_of(bearing: float) -> str:
    """Word bucket for a bearing, in the exact vocabulary the questions use."""
    if abs(bearing) <= C.CENTER_DEGREES:
        return "centered"
    if abs(bearing) <= SIDE_WIDE_DEGREES:
        return "left" if bearing < 0 else "right"
    return "far_left" if bearing < 0 else "far_right"


def encode(state, game_vars, last: dict | None = None,
           focus: dict | None = None, recent: list | None = None,
           lead: dict | None = None) -> dict:
    """state: vizdoom GameState, game_vars: [health, ammo, kills, angle,
    hits_taken?, damage?].

    last: feedback from the previous decision
    {"action": str, "hp_change": float, "ammo_used": float, "kills_change": int}
    so the brain can see the consequences of its last pick.

    focus: current engagement-lock target
    {"id": int, "type": str, "bearing": float, "engaged": int}
    (last-seen bearing while the target is off-screen). None when no lock.

    recent: up to 5 previous feedback dicts, oldest first (history window).

    lead: {"tics", "turn", "cap_deg"} — predict the state this many game
    tics ahead: enemies move along their velocity, the player's facing
    advances by TURN_DEG_PER_TIC*tics in the turn direction (+1 right,
    -1 left, 0 none), capped at cap_deg. None = raw current state.
    """
    health, ammo, kills = (float(game_vars[i]) for i in range(3))
    angle = float(game_vars[3]) if len(game_vars) > 3 else 0.0
    lead_tics = float(lead["tics"]) if lead else 0.0
    angle = _lead_angle(angle, lead)
    # Appended by doom_env (HITS_TAKEN, DAMAGECOUNT). Length-guarded
    # so older recordings / builds without them still decode.
    hits_taken = float(game_vars[4]) if len(game_vars) > 4 else None
    damage = float(game_vars[5]) if len(game_vars) > 5 else None

    px, py = _player(state)

    enemies = []
    visible_ids = {label.object_id for label in (state.labels or [])}
    labels_by_id = {label.object_id: label for label in (state.labels or [])}
    # Screen width for the label x-error: derive from the frame when
    # available (640 wide -> center 320px), else assume 640.
    screen_w = 320
    sb = getattr(state, "screen_buffer", None)
    if sb is not None and getattr(sb, "ndim", 0) == 3:
        screen_w = int(sb.shape[2] if sb.shape[0] <= 4 else sb.shape[1])
    elif sb is not None and getattr(sb, "ndim", 0) == 2:
        screen_w = int(sb.shape[1])
    # Skip non-threats: the player and impact effects.
    IGNORE = {"DoomPlayer", "BulletPuff", "Blood"}
    for o in state.objects or []:
        if o.name in IGNORE:
            continue
        bearing, dist = _rel(px, py, angle, o, lead_tics)
        # Radial velocity: negative = closing in on the player.
        dx = float(o.position_x) + float(o.velocity_x) * lead_tics - px
        dy = float(o.position_y) + float(o.velocity_y) * lead_tics - py
        vx, vy = float(o.velocity_x), float(o.velocity_y)
        closing = (vx * dx + vy * dy) / dist < -1.0
        # Screen-x centering error (pixels, + = right of center) from the
        # label box, so the brain can micro-adjust. Off-screen: None.
        x_err = None
        lb = labels_by_id.get(o.id)
        if o.id in visible_ids and lb is not None:
            x_err = round(float(lb.x) + float(lb.width) / 2 - screen_w / 2)
        enemies.append({
            "id": o.id,
            "type": o.name,
            "bearing": round(bearing, 1),
            "side": side_of(bearing),
            "dist": round(dist),
            "range": _bucket(dist),
            "visible": o.id in visible_ids,
            "closing": closing,
            "x_err": x_err,
        })

    # Most threatening first: visible, then closest. Cap for token budget.
    enemies.sort(key=lambda e: (not e["visible"], e["dist"]))
    enemies = enemies[:MAX_ENEMIES]
    for i, e in enumerate(enemies, 1):
        e["idx"] = i  # stable per-snapshot key for the picked enemy

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
    if recent:
        snap["recent"] = list(recent)
    if lead:
        snap["lead_tics"] = int(lead["tics"])

    return snap
