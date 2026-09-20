"""Defend heuristic: snapshot -> 3-button vector. No API, no model.

Geometry-only baseline for defend_the_center: nearest visible enemy,
fire within ±8°, else turn toward it at 4-tic cadence. Stateless —
`reset_episode` is a no-op kept for call compatibility with play.py.
"""
from . import config as C


def reset_episode() -> None:
    return None


def danger_of(snapshot: dict) -> float:
    """Danger on the 0–2 scale the logs use.

    2.0 = a close enemy is visible, 1.0 = something visible,
    0.0 = nothing.
    """
    enemies = snapshot.get("enemies", [])
    if any(e.get("visible") and e.get("range") == "close" for e in enemies):
        return 2.0
    if any(e.get("visible") for e in enemies):
        return 1.0
    return 0.0


def heuristic_action(snapshot: dict) -> tuple[list, int, str]:
    """Geometry baseline: ([left, right, attack], hold_tics, reason)."""
    enemies = snapshot.get("enemies", [])
    vis = [e for e in enemies if e.get("visible")]
    pool = vis or enemies  # aim at off-screen bearings too
    if not pool:
        return [0, 0, 0], C.TURN_TICS, "heuristic: no target"
    tgt = min(pool, key=lambda e: e["dist"])  # nearest by DISTANCE (anti-spin)
    b = tgt["bearing"]
    if abs(b) <= C.HEURISTIC_FIRE_DEG and snapshot["player"]["ammo"] > 0:
        return [0, 0, 1], C.FIRE_TICS, f"heuristic fire b={b}"
    vec = [1, 0, 0] if b < 0 else [0, 1, 0]  # left / right
    return vec, C.HEURISTIC_TURN_TICS, f"heuristic turn b={b}"
