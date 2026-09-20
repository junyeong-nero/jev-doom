"""Synthetic vizdoom-shaped objects. No engine, no API."""
from types import SimpleNamespace as NS

import pytest

# Defend game vars: [HEALTH, AMMO2, KILLCOUNT, ANGLE, HITS_TAKEN,
# DAMAGECOUNT] (26 bullets, the loadout the 10.20±2.04 suite used)
VARS = [100.0, 26.0, 0.0, 0.0, 0.0, 0.0]

VARS_DEFEND = list(VARS)


def obj(id, name, x, y, vx=0.0, vy=0.0):
    return NS(id=id, name=name, position_x=x, position_y=y,
              velocity_x=vx, velocity_y=vy)


def label(object_id, x=310, width=20, category="Monster"):
    # 640px-wide screen assumed by encoder when screen_buffer is None
    return NS(object_id=object_id, x=x, width=width, object_category=category)


def state(objects, labels=(), game_vars=None):
    return NS(objects=list(objects), labels=list(labels), screen_buffer=None,
              depth_buffer=None,
              game_variables=list(game_vars if game_vars is not None else VARS))


PLAYER = obj(0, "DoomPlayer", 0.0, 0.0)


class FakeGame:
    """Records make_action calls; state is static unless the test swaps it."""

    def __init__(self, st, finished_after=None):
        self.st = st
        self.calls = []  # (action_list, tics)
        self.finished_after = finished_after

    def make_action(self, action, tics):
        self.calls.append((list(action), tics))

    def get_state(self):
        return self.st

    def is_episode_finished(self):
        return (self.finished_after is not None
                and len(self.calls) >= self.finished_after)

    def get_available_buttons(self):
        return [None] * 3


@pytest.fixture
def fake_game():
    return FakeGame
