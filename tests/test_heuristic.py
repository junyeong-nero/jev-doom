from doom import heuristic as policy
from doom.encoder import encode
from tests.conftest import PLAYER, VARS, label, obj, state


def snap_two():
    st = state([PLAYER,
                obj(1, "Zombieman", 100.0, -50.0),   # +26.6 right visible
                obj(2, "Demon", 0.0, 100.0)],         # -90 far_left
               labels=[label(1)])
    return encode(st, VARS)


def setup_function(_):
    policy.reset_episode()


def test_fires_centered_visible_threat():
    st = state([PLAYER, obj(1, "Zombieman", 100.0, 0.0)],  # centered
               labels=[label(1)])
    snap = encode(st, VARS)
    vec, tics, reason = policy.heuristic_action(snap)
    assert vec == [0, 0, 1] and "fire" in reason


def test_no_fire_when_dry_turns_instead():
    dry = [100.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    st = state([PLAYER, obj(1, "Zombieman", 100.0, -50.0)],  # +26.6 right
               labels=[label(1)], game_vars=dry)
    snap = encode(st, dry)
    vec, tics, reason = policy.heuristic_action(snap)
    assert vec == [0, 1, 0] and "turn" in reason


def test_turns_toward_bearing_at_4_tic_cadence():
    snap = snap_two()  # idx1 bearing +26.6 (right)
    vec, tics, reason = policy.heuristic_action(snap)
    assert vec == [0, 1, 0] and tics == 4
    assert "b=26.6" in reason


def test_holds_with_no_targets():
    snap = encode(state([PLAYER]), VARS)
    vec, _, reason = policy.heuristic_action(snap)
    assert vec == [0, 0, 0] and "no target" in reason


def test_aims_at_offscreen_bearing():
    st = state([PLAYER, obj(1, "Demon", -100.0, 0.0)])  # no label
    snap = encode(st, VARS)
    vec, _, reason = policy.heuristic_action(snap)
    assert vec in ([1, 0, 0], [0, 1, 0]) and "turn" in reason


def test_danger_scale():
    assert policy.danger_of({"enemies": []}) == 0.0
    far = {"enemies": [{"visible": True, "range": "far"}]}
    assert policy.danger_of(far) == 1.0
    close = {"enemies": [{"visible": True, "range": "close"}]}
    assert policy.danger_of(close) == 2.0
