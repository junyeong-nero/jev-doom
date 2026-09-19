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
FIRE_TICS = 2           # press held 2 tics (see play.py: always followed by release)
RELEASE_TICS = 2        # release after every shot: pistol is semi-auto, needs re-press
SWEEP_CONFIDENCE = 0.8  # below this, keep sweeping last turn direction (anti-jitter)
CENTER_DEGREES = 12     # |bearing| within this counts as "centered"
CLOSE_DIST = 300.0      # world units: below = close
MID_DIST = 700.0        # below = mid, else far
