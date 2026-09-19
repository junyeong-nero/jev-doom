"""Jev policy: snapshot -> answers -> button vector."""
import time

import httpx

from . import config as C


def decide(client: httpx.Client, snapshot: dict) -> tuple[dict, dict, float]:
    """One system_one call. Returns (answers, usage, latency_ms)."""
    questions = dict(C.QUESTIONS)
    questions["fire"] = {
        **questions["fire"],
        "instructions": C.FIRE_INSTRUCTIONS.format(
            ammo=int(snapshot["player"]["ammo"])),
    }
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


def to_action(answers: dict, snapshot: dict,
              last_turn: list | None = None,
              scenario: str = "defend") -> tuple[list, int, str]:
    """Map answers to ([left, right, attack], hold_tics, reason).

    last_turn: previous turn vector ([1,0,0] or [0,1,0]) for sweep hysteresis.
    """
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

    if aim["confidence"] >= C.SWEEP_CONFIDENCE:
        if aim["choice"] == "left":
            return [1, 0, 0], C.TURN_TICS, "aim left"
        if aim["choice"] == "right":
            return [0, 1, 0], C.TURN_TICS, "aim right"
        return [0, 0, 0], C.TURN_TICS, "aim center"

    # low confidence: keep sweeping instead of jittering
    if last_turn in ([1, 0, 0], [0, 1, 0]):
        return last_turn, C.TURN_TICS, f"sweep conf={aim['confidence']:.2f}"
    return [0, 0, 0], C.TURN_TICS, f"hold conf={aim['confidence']:.2f}"
