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


def skip_sprint():
    policy._corridor_decisions = policy.OPENING_SPRINT


def test_opening_sprint_advances_then_aims():
    snap = snap_two()
    for _ in range(policy.OPENING_SPRINT):
        vec, _, reason = policy.heuristic_action(snap)
        assert vec == [1, 0, 0, 0, 0, 0, 0, 0, 1] and reason == "opening sprint"
    vec, _, reason = policy.heuristic_action(snap)
    assert "turn" in reason and vec[5] == 1  # +26.6 right -> turn_right


def test_reset_episode_restarts_sprint():
    snap = snap_two()
    skip_sprint()
    policy.heuristic_action(snap)
    policy.reset_episode()
    vec, _, reason = policy.heuristic_action(snap)
    assert reason == "opening sprint"


def test_fires_centered_visible_threat():
    st = state([PLAYER, obj(1, "Zombieman", 100.0, 0.0)],  # centered
               labels=[label(1)])
    snap = encode(st, VARS)
    skip_sprint()
    vec, _, reason = policy.heuristic_action(snap)
    assert vec[6] == 1  # ATTACK pressed (plain or dodge-fire strafe)


def test_dodge_fires_on_the_move_when_close():
    # close + visible -> danger 2.0 >= DODGE: strafe_fire, not stationary
    skip_sprint()
    policy._dodge_side = "strafe_left_fire"
    vec, _, reason = policy.heuristic_action(snap_two_centered_close())
    assert vec[6] == 1 and (vec[2] == 1 or vec[3] == 1)
    assert "dodge-fire" in reason


def snap_two_centered_close():
    st = state([PLAYER, obj(1, "Zombieman", 100.0, 0.0)], labels=[label(1)])
    return encode(st, VARS)


def test_no_fire_when_dry_turns_instead():
    dry = [100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0]
    st = state([PLAYER, obj(1, "Zombieman", -100.0, 0.0)],  # bearing 180... use left
               labels=[label(1)], game_vars=dry)
    snap = encode(st, dry)
    skip_sprint()
    vec, tics, reason = policy.heuristic_action(snap)
    assert vec[6] == 0 and "turn" in reason


def test_turns_toward_bearing_at_4_tic_cadence():
    snap = snap_two()  # idx1 bearing +26.6 (right)
    skip_sprint()
    vec, tics, reason = policy.heuristic_action(snap)
    assert vec == [0, 0, 0, 0, 0, 1, 0, 0, 0] and tics == 4
    assert "b=26.6" in reason


def test_advances_with_no_targets():
    snap = encode(state([PLAYER]), VARS)
    skip_sprint()
    vec, _, reason = policy.heuristic_action(snap)
    assert vec[0] == 1 and "no target" in reason


def test_switches_to_shotgun_when_available():
    armed = [100.0, 52.0, 0.0, 0.0, 0.0, 0.0, 2.0, 10.0, 1.0, 5.0]
    snap = encode(state([PLAYER]), armed)
    skip_sprint()
    vec, _, reason = policy.heuristic_action(snap)
    assert vec[7] == 1 and "shotgun" in reason


def test_faces_offscreen_shooter_after_damage():
    # d1 warms the HITS_TAKEN baseline; d2 takes a hit with nothing
    # visible -> turn toward the off-screen bearing.
    st = state([PLAYER, obj(1, "Demon", -100.0, 0.0)])  # bearing 180/left, no label
    skip_sprint()
    policy.heuristic_action(encode(st, VARS))
    hot = list(VARS)
    hot[4] = 1.0  # HITS_TAKEN rose
    vec, _, reason = policy.heuristic_action(encode(st, hot))
    assert (vec[4] == 1 or vec[5] == 1) and "damage" in reason


def test_danger_scale():
    assert policy.danger_of({"enemies": []}) == 0.0
    far = {"enemies": [{"visible": True, "range": "far"}]}
    assert policy.danger_of(far) == 1.0
    close = {"enemies": [{"visible": True, "range": "close"}]}
    assert policy.danger_of(close) == 2.0
