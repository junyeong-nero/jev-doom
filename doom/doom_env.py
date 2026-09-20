"""VizDoom game factory."""
import os

import vizdoom as vzd


def _scenarios_dir() -> str:
    return os.path.join(os.path.dirname(vzd.__file__), "scenarios")


SCENARIOS = {
    "defend": {
        "wad": "defend_the_center.wad",
        "buttons": [vzd.Button.TURN_LEFT, vzd.Button.TURN_RIGHT, vzd.Button.ATTACK],
        "variables": [
            vzd.GameVariable.HEALTH,
            vzd.GameVariable.AMMO2,
            vzd.GameVariable.KILLCOUNT,
            vzd.GameVariable.ANGLE,
        ],
    },
    "basic": {
        "wad": "basic.wad",
        "buttons": [vzd.Button.MOVE_LEFT, vzd.Button.MOVE_RIGHT, vzd.Button.ATTACK],
        "variables": [
            vzd.GameVariable.HEALTH,
            vzd.GameVariable.AMMO2,
            vzd.GameVariable.KILLCOUNT,
            vzd.GameVariable.ANGLE,
        ],
    },
    "simple": {
        "wad": "simpler_basic.wad",
        "buttons": [vzd.Button.MOVE_LEFT, vzd.Button.MOVE_RIGHT, vzd.Button.ATTACK],
        "variables": [
            vzd.GameVariable.HEALTH,
            vzd.GameVariable.AMMO2,
            vzd.GameVariable.KILLCOUNT,
            vzd.GameVariable.ANGLE,
        ],
        "skill": 3,
    },
    "corridor": {
        "wad": "deadly_corridor.wad",
        "buttons": [
            vzd.Button.MOVE_FORWARD, vzd.Button.MOVE_BACKWARD,
            vzd.Button.MOVE_LEFT, vzd.Button.MOVE_RIGHT,
            vzd.Button.TURN_LEFT, vzd.Button.TURN_RIGHT,
            vzd.Button.ATTACK,
            vzd.Button.SPEED,  # appended: ATTACK stays idx 6 (see ATTACK_IDX)
        ],
        "variables": [
            vzd.GameVariable.HEALTH,
            vzd.GameVariable.AMMO2,
            vzd.GameVariable.KILLCOUNT,
            vzd.GameVariable.ANGLE,
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
