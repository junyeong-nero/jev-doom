"""Shared config: API key, model, Jev questions, thresholds."""
import os
from pathlib import Path


def _load_dotenv() -> None:
    p = Path(__file__).resolve().parents[1] / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

API_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_APIKEY", "")
API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

FIRE_INSTRUCTIONS = (
    "Should the player shoot RIGHT NOW? "
    "Ammo remaining: {ammo} bullets. "
    "Say yes when an enemy is roughly centered ahead and likely to be hit. "
    "When ammo is plentiful, prefer shooting at centered visible enemies "
    "even at longer range; when ammo is almost out, only shoot sure hits."
)

QUESTIONS = {
    "aim": {
        "type": "choice",
        "instructions": (
            "Which sector holds the most threatening enemy? "
            "Prefer close enemies over far ones, and visible (on-screen) enemies "
            "over off-screen ones. Sectors are relative to where the player faces."
        ),
        "criteria": {
            "left": "Biggest threat is to the left of where the player faces",
            "center": "Biggest threat is straight ahead",
            "right": "Biggest threat is to the right of where the player faces",
        },
    },
    "fire": {
        "type": "noul",
        "instructions": FIRE_INSTRUCTIONS,  # formatted with ammo at call time
    },
    "danger": {
        "type": "score",
        "instructions": "How much danger is the player in from nearby enemies?",
        "criteria": [
            "Safe: no enemy close",
            "Caution: enemies approaching or nearby",
            "Critical: enemy very close and/or health low",
        ],
    },
}

# Decision thresholds / timing
FIRE_THRESHOLD = {"defend": 0.65, "basic": 0.5, "simple": 0.5}  # 50 bullets vs 400HP -> volume
TURN_TICS = 16           # ~7 deg per turn decision (turn rate is ~0.44 deg/tic)
MOVE_TICS = 8            # corridor locomotion hold per decision
FIRE_TICS = 2           # press held 2 tics (see play.py: always followed by release)
RELEASE_TICS = 2        # release after every shot: pistol is semi-auto, needs re-press
SWEEP_CONFIDENCE = 0.8  # below this, keep sweeping last turn direction (anti-jitter)
CENTER_DEGREES = 12     # |bearing| within this counts as "centered"
CLOSE_DIST = 300.0      # world units: below = close
MID_DIST = 700.0        # below = mid, else far

# Attack button index per scenario (for press+release handling in play.py)
ATTACK_IDX = {"defend": 2, "basic": 2, "simple": 2, "corridor": 6}

CORRIDOR_QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": (
            "Pick ONE action for a lone marine fighting down a corridor "
            "against 6 armed shooters. Goal: push forward, kill them, survive. "
            "Ammo remaining: {ammo} bullets. "
            "Standing in the open while firing gets you killed: if enemies "
            "are shooting at you, strafe sideways to dodge. "
            "Attack only when an enemy is centered ahead and visible; "
            "otherwise move or turn toward the biggest threat. "
            "path shows nearby walls per sector (wall = blocked that way): "
            "advance only when center is open."
        ),
        "criteria": {
            "advance": "Move forward down the corridor (path is clear)",
            "retreat": "Back away from a close threat",
            "strafe_left": "Sidestep left: dodges incoming fire, keeps aim",
            "strafe_right": "Sidestep right: dodges incoming fire, keeps aim",
            "turn_left": "Rotate left toward the threat",
            "turn_right": "Rotate right toward the threat",
            "attack": "Fire standing still (accurate, but exposed)",
            "strafe_left_fire": "Sidestep left while firing (dodges + shoots)",
            "strafe_right_fire": "Sidestep right while firing (dodges + shoots)",
        },
    },
    "danger": {
        "type": "score",
        "instructions": "How much danger is the player in from nearby enemies?",
        "criteria": [
            "Safe: no enemy close",
            "Caution: enemies approaching or nearby",
            "Critical: enemy very close and/or health low",
        ],
    },
}

# corridor choice -> (button vector, hold tics); buttons =
# [FWD, BACK, LEFT, RIGHT, TLEFT, TRIGHT, ATTACK]
CORRIDOR_ACTIONS = {
    "advance": ([1, 0, 0, 0, 0, 0, 0], MOVE_TICS, "advance"),
    "retreat": ([0, 1, 0, 0, 0, 0, 0], MOVE_TICS, "retreat"),
    "strafe_left": ([0, 0, 1, 0, 0, 0, 0], MOVE_TICS, "strafe left"),
    "strafe_right": ([0, 0, 0, 1, 0, 0, 0], MOVE_TICS, "strafe right"),
    "turn_left": ([0, 0, 0, 0, 1, 0, 0], TURN_TICS, "turn left"),
    "turn_right": ([0, 0, 0, 0, 0, 1, 0], TURN_TICS, "turn right"),
    "attack": ([0, 0, 0, 0, 0, 0, 1], 8, "fire"),  # 8-tic burst: ~2 bullets via auto-refire
    "strafe_left_fire": ([0, 0, 1, 0, 0, 0, 1], 8, "strafe left + fire"),
    "strafe_right_fire": ([0, 0, 0, 1, 0, 0, 1], 8, "strafe right + fire"),
}

# Code-side survival reflex: at/above this danger, stationary picks
# (advance/retreat/attack) become strafes. Jev aims, code dodges.
DODGE_DANGER = 1.7
