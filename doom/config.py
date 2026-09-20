"""Shared config: corridor geometry thresholds, action maps, tic constants.

Heuristic-only: no API key, no network calls. Everything here feeds the
geometry baseline in `policy.heuristic_action`.
"""

HEURISTIC_FIRE_DEG = 8  # fire when the picked threat is this centered
HEURISTIC_TURN_TICS = 4  # re-aim cadence: short holds, decide every few tics

# Decision thresholds / timing (35 tics = 1 game second)
TURN_TICS = 4            # fallback turn hold
TURN_DEG_PER_TIC = 0.44  # measured turn rate
MOVE_TICS = 4            # locomotion hold per decision
FIRE_TICS = 2           # press held 2 tics (always followed by release)
RELEASE_TICS = 2        # release after every shot: pistol is semi-auto, needs re-press
SWITCH_TICS = 6         # weapon-switch press hold (raise animation)
CENTER_DEGREES = 10     # |bearing| within this counts as "centered"
CLOSE_DIST = 300.0      # world units: below = close
MID_DIST = 700.0        # below = mid, else far

# Attack button index (corridor 9-vector; ATTACK stays index 6 by construction)
ATTACK_IDX = {"corridor": 6}

# choice -> (button vector, hold tics); buttons =
# [FWD, BACK, LEFT, RIGHT, TLEFT, TRIGHT, ATTACK, SELECT_WEAPON3, SPEED].
# Locomotion (advance/retreat/strafe, incl. firing strafes) always holds
# SPEED: Doom has no stamina, it's free speed.
# (SELECT_WEAPON3 at index 7 keeps ATTACK at index 6.)
CORRIDOR_ACTIONS = {
    "advance": ([1, 0, 0, 0, 0, 0, 0, 0, 1], MOVE_TICS, "advance"),
    "retreat": ([0, 1, 0, 0, 0, 0, 0, 0, 1], MOVE_TICS, "retreat"),
    "strafe_left": ([0, 0, 1, 0, 0, 0, 0, 0, 1], MOVE_TICS, "strafe left"),
    "strafe_right": ([0, 0, 0, 1, 0, 0, 0, 0, 1], MOVE_TICS, "strafe right"),
    "turn_left": ([0, 0, 0, 0, 1, 0, 0, 0, 0], TURN_TICS, "turn left"),
    "turn_right": ([0, 0, 0, 0, 0, 1, 0, 0, 0], TURN_TICS, "turn right"),
    "attack": ([0, 0, 0, 0, 0, 0, 1, 0, 0], 4, "fire"),  # 4-tic press + release below
    "strafe_left_fire": ([0, 0, 1, 0, 0, 0, 1, 0, 1], 4, "strafe left + fire"),
    "strafe_right_fire": ([0, 0, 0, 1, 0, 0, 1, 0, 1], 4, "strafe right + fire"),
    "switch_to_shotgun": ([0, 0, 0, 0, 0, 0, 0, 1, 0], SWITCH_TICS,
                          "switch to shotgun"),
}

# Code-side survival reflex: at/above this danger, stationary attacks
# become firing strafes. Geometry aims, reflexes dodge.
DODGE_DANGER = 1.7
