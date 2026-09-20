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
            "You are a lone marine with a pistol fighting down a corridor "
            "against 6 armed shooters at max aggression (they focus fire). "
            "Goal: push forward, kill them, survive. "
            "HOW TO READ THE STATE: bearing is degrees, negative = LEFT, "
            "positive = RIGHT, 0 = straight ahead; |bearing| <= 12 counts as "
            "centered. dist is world units (~400 = mid-corridor). visible = "
            "on screen right now. closing = moving toward you. path shows "
            "nearby walls per sector (wall = blocked that way). last shows "
            "what your previous pick cost: hp_change (damage taken), "
            "ammo_used, kills_change. "
            "YOUR ARSENAL: one pistol, {ammo} bullets left. It fires short "
            "bursts; every enemy needs multiple hits. Do not waste bullets "
            "at far range. "
            "ENEMIES: all hitscan shooters. Zombieman is weak; ShotgunGuy is "
            "lethal up close; ChaingunGuy deals sustained fire; Demon is a "
            "melee tank. Kill order: closest VISIBLE shooter first. "
            "TACTICS: standing in overlapping sightlines kills you in ~3 "
            "seconds. Count visible enemies: if 3+ are visible at once you "
            "are IN a kill-zone: pick advance and run out of it, do not "
            "trade fire from a standstill. If last.hp_change is dropping "
            "fast, you are standing in fire: strafe sideways (keeps your "
            "aim) or advance out of it. Retreat is a dead-end wall, never "
            "retreat twice in a row. "
            "Advance only when center path is open. If your shots keep "
            "missing (ammo_used up, kills_change 0), close distance first."
        ),
        "criteria": {
            "advance": "Sprint forward: path center open, no close threat",
            "retreat": "EMERGENCY only: one step back from a point-blank threat",
            "strafe_left": "Sidestep left under fire, or circle toward a left threat",
            "strafe_right": "Sidestep right under fire, or circle toward a right threat",
            "turn_left": "Threat is far left: rotate to face it",
            "turn_right": "Threat is far right: rotate to face it",
            "attack": "Clean kill shot: enemy centered AND visible AND close/mid",
            "strafe_left_fire": "Return fire while sidestepping left (default under fire)",
            "strafe_right_fire": "Return fire while sidestepping right (default under fire)",
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
