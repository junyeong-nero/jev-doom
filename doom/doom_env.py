"""VizDoom game factory."""
import os

import vizdoom as vzd


def _scenarios_dir() -> str:
    return os.path.join(os.path.dirname(vzd.__file__), "scenarios")


def _damage_variables() -> list:
    """HITS_TAKEN / DAMAGECOUNT for issue-3 cover reflexes.

    Verified present in this vizdoom build (GameVariable.HITS_TAKEN,
    GameVariable.DAMAGECOUNT); getattr guard keeps older builds working
    by silently falling back to hp-delta damage detection.
    """
    out = []
    for name in ("HITS_TAKEN", "DAMAGECOUNT"):
        var = getattr(vzd.GameVariable, name, None)
        if var is not None:
            out.append(var)
    return out


def _base_variables() -> list:
    return [
        vzd.GameVariable.HEALTH,
        vzd.GameVariable.AMMO2,
        vzd.GameVariable.KILLCOUNT,
        vzd.GameVariable.ANGLE,
        *_damage_variables(),
    ]


SCENARIOS = {
    "defend": {
        "wad": "defend_the_center.wad",
        "buttons": [vzd.Button.TURN_LEFT, vzd.Button.TURN_RIGHT, vzd.Button.ATTACK],
        "variables": _base_variables(),
    },
    "basic": {
        "wad": "basic.wad",
        "buttons": [vzd.Button.MOVE_LEFT, vzd.Button.MOVE_RIGHT, vzd.Button.ATTACK],
        "variables": _base_variables(),
    },
    "simple": {
        "wad": "simpler_basic.wad",
        "buttons": [vzd.Button.MOVE_LEFT, vzd.Button.MOVE_RIGHT, vzd.Button.ATTACK],
        "variables": _base_variables(),
        "skill": 3,
    },
    "corridor": {
        "wad": "deadly_corridor.wad",
        "buttons": [
            vzd.Button.MOVE_FORWARD, vzd.Button.MOVE_BACKWARD,
            vzd.Button.MOVE_LEFT, vzd.Button.MOVE_RIGHT,
            vzd.Button.TURN_LEFT, vzd.Button.TURN_RIGHT,
            vzd.Button.ATTACK,
            # Appended at END so ATTACK stays index 6. Slot 3 = shotgun
            # (slot 2 is the starting pistol); ChaingunGuys/ShotgunGuys
            # drop their guns on death, so this gets used mid-episode.
            vzd.Button.SELECT_WEAPON3,
            vzd.Button.SPEED,  # free run speed, no stamina (issue #5)
        ],
        # Weapon vars AFTER the damage counters ([6..9]) so encoder
        # indexes [4],[5] keep reading HITS_TAKEN/DAMAGECOUNT (issue #3).
        "variables": _base_variables() + [
            vzd.GameVariable.SELECTED_WEAPON,
            vzd.GameVariable.SELECTED_WEAPON_AMMO,
            vzd.GameVariable.WEAPON3,  # shotgun owned (0/1)
            vzd.GameVariable.AMMO1,  # shotgun shells
        ],
        "skill": 5,  # as designed: fast monsters, death_penalty 100
        "depth": True,  # depth buffer for cover features
    },
}


def make_game(scenario: str = "defend", visible: bool = False,
              timeout_tics: int = 2100) -> vzd.DoomGame:
    """Create and init a game. Caller owns close()."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}, pick from {list(SCENARIOS)}")
    spec = SCENARIOS[scenario]
    game = vzd.DoomGame()
    game.set_doom_scenario_path(os.path.join(_scenarios_dir(), spec["wad"]))
    game.set_doom_map("map01")
    game.set_available_buttons(spec["buttons"])
    game.set_available_game_variables(spec["variables"])
    game.set_labels_buffer_enabled(True)
    game.set_objects_info_enabled(True)
    if spec.get("depth"):
        game.set_depth_buffer_enabled(True)
    game.set_window_visible(visible)
    game.set_mode(vzd.Mode.PLAYER)
    game.set_doom_skill(spec.get("skill", 3))
    game.set_episode_timeout(timeout_tics)
    game.init()
    return game
