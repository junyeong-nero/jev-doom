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

#: Opening sprint: first N corridor decisions always advance, to clear
#: the spawn kill-zone before fighting. (Learned from a scripted rush
#: scoring +495 vs -16 dodging in place.)
OPENING_SPRINT = 6


def reset_episode() -> None:
    global _corridor_decisions
    _corridor_decisions = 0


def _corridor_action(answers: dict, snapshot: dict) -> tuple[list, int, str]:
    global _dodge_side, _corridor_decisions
    _corridor_decisions += 1
    if _corridor_decisions <= OPENING_SPRINT:
        vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
        return vec, tics, "opening sprint"
    pick = answers["action"]
    choice, conf = pick["choice"], pick["confidence"]
    danger = answers["danger"]["score"]

    if choice in ("attack", "strafe_left_fire", "strafe_right_fire"):
        if snapshot["player"]["ammo"] <= 0 or not snapshot["center_visible"]:
            vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
            return vec, tics, "downgraded fire (no target/ammo)"
        if danger >= C.DODGE_DANGER and choice == "attack":
            # Never stand still trading fire with 6 shotgunners.
            _dodge_side = ("strafe_right"
                           if _dodge_side == "strafe_left" else "strafe_left")
            vec, tics, _ = C.CORRIDOR_ACTIONS[_dodge_side]
            return vec, tics, f"dodge (attack@{danger:.1f})"
        vec, tics, reason = C.CORRIDOR_ACTIONS[choice]
        return vec, tics, reason

    # Survival reflex: under heavy fire, don't stand still.
    if danger >= C.DODGE_DANGER and choice in ("advance", "retreat"):
        _dodge_side = ("strafe_right"
                       if _dodge_side == "strafe_left" else "strafe_left")
        vec, tics, _ = C.CORRIDOR_ACTIONS[_dodge_side]
        return vec, tics, f"dodge ({choice}@{danger:.1f})"

    if choice in C.CORRIDOR_ACTIONS:
        vec, tics, reason = C.CORRIDOR_ACTIONS[choice]
        if conf < C.SWEEP_CONFIDENCE:
            vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
            return vec, tics, f"advance (low conf {conf:.2f})"
        return vec, tics, reason
    vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
    return vec, tics, "advance (fallback)"


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
    return tics, tgt["bearing"]


def to_action(answers: dict, snapshot: dict,
              last_turn: list | None = None,
              scenario: str = "defend",
              after_turn: bool = False) -> tuple[list, int, str]:
    """Map answers to ([left, right, attack], hold_tics, reason).

    last_turn: previous turn vector ([1,0,0] or [0,1,0]) for sweep hysteresis.
    after_turn: previous action was a turn -> one observation decision
        (anti-overshoot) before turning again.
    """
    if scenario == "corridor":
        return _corridor_action(answers, snapshot)
    ammo = snapshot["player"]["ammo"]
    aim = answers["aim"]
    fire_p = answers["fire"]["noul"]
    threshold = C.FIRE_THRESHOLD.get(scenario, 0.65)

    attack = (
        ammo > 0
        and fire_p >= threshold
        and snapshot["center_visible"]
    )
    if attack:
        return [0, 0, 1], C.FIRE_TICS, f"fire p={fire_p:.2f}"

    if after_turn:
        # One settling observation after a turn (anti-overshoot); the next
        # decision re-aims from a fresh snapshot.
        return [0, 0, 0], C.OBSERVE_TICS, "observe after turn"

    if aim["confidence"] >= C.SWEEP_CONFIDENCE:
        if aim["choice"] == "left":
            tics, b = _turn_tics(snapshot, "left")
            if b is None:
                return [1, 0, 0], tics, "aim left (no visible target)"
            return [1, 0, 0], tics, f"aim left b={b} tics={tics}"
        if aim["choice"] == "right":
            tics, b = _turn_tics(snapshot, "right")
            if b is None:
                return [0, 1, 0], tics, "aim right (no visible target)"
            return [0, 1, 0], tics, f"aim right b={b} tics={tics}"
        return [0, 0, 0], C.TURN_TICS, "aim center"

    # low confidence: keep sweeping instead of jittering
    if last_turn in ([1, 0, 0], [0, 1, 0]):
        return last_turn, C.TURN_TICS, f"sweep conf={aim['confidence']:.2f}"
    return [0, 0, 0], C.TURN_TICS, f"hold conf={aim['confidence']:.2f}"
