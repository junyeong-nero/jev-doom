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

API_KEY = os.environ.get("TYPESAFE_API_KEY", "")
API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

FIRE_INSTRUCTIONS = (
    "Decide whether to shoot RIGHT NOW. "
    "Apply this exact rule, no judgment calls: "
    "if any enemy in enemies has |bearing| <= 10 AND visible = true, "
    "pick shoot. Otherwise pick hold. "
    "Ammo remaining: {ammo} bullets; every enemy needs multiple hits, "
    "so keep picking shoot on a centered visible enemy across decisions "
    "(sustained fire — do not conserve ammo when the rule fires)."
)

FOCUS_INSTRUCTIONS = (
    "TARGET LOCK: state.focus is the enemy you are already engaging "
    "({{id, type, last-seen bearing, engaged count}}, or null when no lock). "
    "Hold focus until the target is dead (it disappears from enemies AND "
    "kills ticked up — note kills_change attribution is approximate, a death "
    "tick may credit a different nearby enemy) or fully lost (gone from "
    "enemies for several decisions). When focus is off-screen, prefer "
    "turning back toward its last-seen bearing to re-acquire it instead of "
    "switching to a new target. Finish one enemy before starting another."
)

# ultrafast-style structured instructions: every question in a request
# shares RULES_COMMON (how to read the state), plus its own goal. Words
# here MUST match the snapshot vocabulary exactly (side buckets, ranges):
# jev-flappy-bird measured a large drop when criteria and state disagreed.
RULES_COMMON = [
    "bearing: degrees, negative = LEFT, positive = RIGHT, 0 = straight ahead.",
    "side: the bearing in words: far_left, left, centered, right, far_right. "
    "centered means |bearing| <= 10.",
    "range: close (< 300 units), mid (< 700), far. dist is world units.",
    "visible: on screen right now. closing: moving toward you.",
    "idx: the enemy's key in the target question.",
    "focus: the enemy you picked last time (null when none), with its "
    "last-seen bearing. Finish it before switching unless a closer visible "
    "enemy appears.",
    "recent: your previous decisions, oldest first, with what each cost "
    "(hp_change, ammo_used, kills_change). last is the most recent one.",
    "lead_tics: the state is predicted this many game tics ahead, to the "
    "moment your answer lands.",
]

TARGET_GOAL = (
    "Pick the enemy to engage right now by its idx. Prefer close over far, "
    "visible over off-screen, centered over sides, and keep focus unless a "
    "better target appeared. Pick none only when no enemy is listed."
)

FIRE_GOAL = (
    "Decide whether to shoot RIGHT NOW. Apply this exact rule: if any enemy "
    "has side = centered AND visible = true, pick shoot; otherwise pick hold. "
    "Ammo: {ammo} bullets; every enemy needs several hits, so keep picking "
    "shoot on a centered visible enemy across consecutive decisions."
)

DANGER_GOAL = "How much danger is the player in from nearby enemies?"

QUESTIONS = {
    "fire": {
        "type": "choice",
        "instructions": FIRE_INSTRUCTIONS,  # formatted with ammo at call time
        "criteria": {
            "shoot": "An enemy has side = centered AND visible = true: "
                     "fire now",
            "hold": "No enemy is both centered and visible: hold fire "
                    "(aim first, save ammo)",
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

# Decision thresholds / timing (defend_the_center, 3 buttons)
# FIRE_THRESHOLD is legacy: defend fire is now a relative
# Choice (shoot/hold), so the Noul threshold no longer gates firing.
FIRE_THRESHOLD = 0.4
TURN_TICS = 4            # fallback turn hold: their 4-tic cadence
TURN_DEG_PER_TIC = 0.44  # measured turn rate: proportional holds = round(|bearing| / 0.44)
TURN_TICS_MIN = 2        # smallest turn: micro-adjusts land at 2-4 tics
TURN_TICS_MAX = 4        # cap a single hold: re-aim every 4 tics like their bot
FIRE_TICS = 2           # press held 2 tics (see play.py: always followed by release)
RELEASE_TICS = 2        # release after every shot: pistol is semi-auto, needs re-press
# Issue-21 sustained fire (defend ONLY): chained-burst tuning.
# Consecutive shoot picks on a centered+visible, non-far target extend the
# fire hold by BURST_STEP_TICS each, up to BURST_MAX_TICS. At/above
# BURST_DANGER_HI the bot fires single bursts only: under heavy fire the
# next decision must come fast (turn/move corrections), so survival wins
# over volume.
BURST_MAX_TICS = 12     # hard cap: one fire hold never exceeds ~1/3 game-second
BURST_STEP_TICS = 2     # +hold per consecutive shoot decision
BURST_DANGER_HI = 1.7   # at/above: single bursts only (same 0-2 danger scale)
CENTER_DEGREES = 10     # |bearing| within this counts as "centered" (matches ±8-10 rule-style firing)
CLOSE_DIST = 300.0      # world units: below = close
MID_DIST = 700.0        # below = mid, else far

# Attack button index (defend: [TURN_LEFT, TURN_RIGHT, ATTACK]).
ATTACK_IDX = 2

# Defend geometry baseline (heuristic brain, no API): fire when the
# picked threat is this centered, else turn toward it at this cadence.
HEURISTIC_FIRE_DEG = 8  # fire when the picked threat is this centered
HEURISTIC_TURN_TICS = 4  # re-aim cadence: short holds, decide every few tics
