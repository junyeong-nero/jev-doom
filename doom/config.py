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

QUESTIONS = {
    "aim": {
        "type": "choice",
        "instructions": (
            "Which sector holds the most threatening enemy? "
            "Prefer close enemies over far ones, and visible (on-screen) enemies "
            "over off-screen ones. Sectors are relative to where the player faces. "
            "Visible enemies also report x_err: horizontal screen pixels from "
            "center (+ means right of center, 0 = centered); prefer nearly "
            "centered targets. "
            + FOCUS_INSTRUCTIONS
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
TURN_TICS = 16           # fallback turn hold when no visible target bears on the sector
TURN_DEG_PER_TIC = 0.44  # measured turn rate: proportional holds = round(|bearing| / 0.44)
TURN_TICS_MIN = 2        # smallest turn: micro-adjusts land at 2-4 tics
TURN_TICS_MAX = 100      # cap a single hold (~44 deg): re-aim instead of freezing
OBSERVE_TICS = 4         # settle/observe hold after a turn (anti-overshoot)
MOVE_TICS = 8            # corridor locomotion hold per decision
FIRE_TICS = 2           # press held 2 tics (see play.py: always followed by release)
RELEASE_TICS = 2        # release after every shot: pistol is semi-auto, needs re-press
SWITCH_TICS = 6         # weapon-switch press hold (raise animation)
SWEEP_CONFIDENCE = 0.8  # below this, keep sweeping last turn direction (anti-jitter)
CENTER_DEGREES = 6      # |bearing| within this counts as "centered"
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
            "positive = RIGHT, 0 = straight ahead; |bearing| <= 6 counts as "
            "centered. dist is world units (~400 = mid-corridor). visible = "
            "on screen right now. closing = moving toward you. path shows "
            "nearby walls per sector (wall = blocked that way). last shows "
            "what your previous pick cost: hp_change (damage taken), "
            "ammo_used, kills_change. player.hits_taken and player.damage "
            "are cumulative counters of hits suffered: rising values mean "
            "you are being shot even when no enemy is visible. "
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
            "aim) or advance out of it. MOVEMENT: every advance, retreat "
            "and strafe step auto-runs at full SPEED (free speed, no "
            "stamina) — strafing dodges while keeping your aim. "
            "Retreat is a single kiting step back, not an escape: allowed "
            "when a close threat is straight ahead "
            "(sectors.center.nearest is close, e.g. a Demon closing in) "
            "so you can shoot it while it closes the gap. Never retreat "
            "during the opening push (the spawn wall is directly behind "
            "you), and never twice in a row (backing blind into walls). "
            "COVER: path shows walls per sector and I sidestep toward a "
            "wall sector on my own when hit with nothing visible ahead, "
            "so help me: if you see path.left or path.right is wall, "
            "prefer strafing to that wall side to break line-of-sight "
            "instead of standing center. If enemies disappear from view "
            "right after I move sideways, I found cover: hold the angle, "
            "peek with strafe_fire, do not walk back into the open. "
            "If I take damage with nothing visible ahead, the shot came "
            "from a flank: turn toward the side where enemies were last "
            "seen rather than firing blind. "
            "Advance only when center path is open. If your shots keep "
            "missing (ammo_used up, kills_change 0), close distance first. "
            "PICKUPS: pickups holds the nearest health / ammo / weapon / "
            "armor item each (or null when none): dist in world units, "
            "bearing in degrees (negative = LEFT, positive = RIGHT), "
            "visible = on screen now. Items are collected by walking over "
            "them. player.shells is shotgun shells; player.shotgun_owned "
            "tells if you carry the shotgun; player.selected_weapon is the "
            "current weapon slot (2 = pistol, 3 = shotgun). "
            "PRIORITY: (1) health below ~40 with a health item nearby: "
            "detour to grab it, even under fire. (2) pistol nearly empty "
            "(5 or fewer bullets) with ammo nearby: grab it before trading "
            "fire. (3) a dropped shotgun/chaingun nearby: detour to pick "
            "it up; once the shotgun is owned and shells are available, "
            "pick switch_to_shotgun. "
            "TARGET LOCK: state.focus is the enemy you are already engaging "
            "(or null when no lock). Hold it until dead (disappears AND kills "
            "ticked up — kills_change attribution is approximate) or fully "
            "lost; when it leaves the screen, turn back toward its last-seen "
            "bearing to re-acquire instead of switching targets."
        ),
        "criteria": {
            "advance": "Sprint forward: path center open, no close threat",
            "retreat": "Kiting step back: close threat straight ahead, "
                       "single step only (never in the opening push)",
            "strafe_left": "Sidestep left under fire, circle toward a left threat, or hug left wall cover",
            "strafe_right": "Sidestep right under fire, circle toward a right threat, or hug right wall cover",
            "turn_left": "Threat is far left: rotate to face it",
            "turn_right": "Threat is far right: rotate to face it",
            "attack": "Clean kill shot: enemy centered AND visible AND close/mid",
            "strafe_left_fire": "Return fire while sidestepping left (default under fire)",
            "strafe_right_fire": "Return fire while sidestepping right (default under fire)",
            "switch_to_shotgun": "Shotgun owned and shells available: switch to the bigger gun now",
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
# [FWD, BACK, LEFT, RIGHT, TLEFT, TRIGHT, ATTACK, SELECT_WEAPON3, SPEED].
# Locomotion (advance/retreat/strafe, incl. firing strafes) always holds
# SPEED: Doom has no stamina, it's free speed — no Jev decision needed.
# (SELECT_WEAPON3 at index 7 keeps ATTACK at index 6.)
CORRIDOR_ACTIONS = {
    "advance": ([1, 0, 0, 0, 0, 0, 0, 0, 1], MOVE_TICS, "advance"),
    "retreat": ([0, 1, 0, 0, 0, 0, 0, 0, 1], MOVE_TICS, "retreat"),
    "strafe_left": ([0, 0, 1, 0, 0, 0, 0, 0, 1], MOVE_TICS, "strafe left"),
    "strafe_right": ([0, 0, 0, 1, 0, 0, 0, 0, 1], MOVE_TICS, "strafe right"),
    "turn_left": ([0, 0, 0, 0, 1, 0, 0, 0, 0], TURN_TICS, "turn left"),
    "turn_right": ([0, 0, 0, 0, 0, 1, 0, 0, 0], TURN_TICS, "turn right"),
    "attack": ([0, 0, 0, 0, 0, 0, 1, 0, 0], 8, "fire"),  # 8-tic burst: ~2 bullets via auto-refire
    "strafe_left_fire": ([0, 0, 1, 0, 0, 0, 1, 0, 1], 8, "strafe left + fire"),
    "strafe_right_fire": ([0, 0, 0, 1, 0, 0, 1, 0, 1], 8, "strafe right + fire"),
    "switch_to_shotgun": ([0, 0, 0, 0, 0, 0, 0, 1, 0], SWITCH_TICS,
                          "switch to shotgun"),
}

# Code-side survival reflex: at/above this danger, stationary picks
# (advance/retreat/attack) become strafes. Jev aims, code dodges.
DODGE_DANGER = 1.7
