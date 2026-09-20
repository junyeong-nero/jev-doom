from doom import play
from tests.conftest import PLAYER, VARS, FakeGame, label, obj, state


def run_track(st, target_id, fire_ok, n_calls=3):
    game = FakeGame(st)
    calls = {"n": 0}

    def done():
        calls["n"] += 1
        return calls["n"] > n_calls

    play._EXT_PACE_S = 0  # no real-time pacing in tests
    tics = play._track(game, [0, 0, 0], None, done, target_id, fire_ok)
    return game.calls, tics


def test_track_turns_toward_offcenter_target():
    st = state([PLAYER, obj(1, "Demon", 100.0, -50.0)], labels=[label(1)])  # +26.6
    calls, tics = run_track(st, 1, fire_ok=True)
    assert calls and all(a == [0, 1, 0] for a, _ in calls)
    assert tics == sum(t for _, t in calls)


def test_track_fires_only_when_authorized_and_centered():
    st = state([PLAYER, obj(1, "Demon", 100.0, 0.0)], labels=[label(1)])
    calls, _ = run_track(st, 1, fire_ok=True)
    assert [0, 0, 1] in [a for a, _ in calls]
    assert [0, 0, 0] in [a for a, _ in calls]  # release after each press
    calls, _ = run_track(st, 1, fire_ok=False)
    assert all(a == [0, 0, 0] for a, _ in calls)


def test_track_no_fire_when_centered_but_offscreen_or_dry():
    st = state([PLAYER, obj(1, "Demon", 100.0, 0.0)])  # no label -> invisible
    calls, _ = run_track(st, 1, fire_ok=True)
    assert all(a == [0, 0, 0] for a, _ in calls)
    dry = [100.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    st = state([PLAYER, obj(1, "Demon", 100.0, 0.0)], labels=[label(1)], game_vars=dry)
    calls, _ = run_track(st, 1, fire_ok=True)
    assert all(a == [0, 0, 0] for a, _ in calls)


def test_track_falls_back_to_cur_without_target():
    st = state([PLAYER])
    game = FakeGame(st)
    n = {"n": 0}

    def done():
        n["n"] += 1
        return n["n"] > 2

    play._EXT_PACE_S = 0
    play._track(game, [1, 0, 0], None, done, None, False)
    assert game.calls and all(a == [1, 0, 0] for a, _ in game.calls)


def test_lead_from_focus_and_cur():
    assert play._lead(0.0, None, [0, 0, 0], "defend") is None
    # tracker will turn right toward a +30 focus, capped at 30 degrees
    assert play._lead(10.4, {"bearing": 30.0}, [0, 0, 0], "defend") == {
        "tics": 10, "turn": 1, "cap_deg": 30.0}
    assert play._lead(10.4, {"bearing": -3.0}, [0, 0, 0], "defend") == {
        "tics": 10, "turn": 0, "cap_deg": None}
    # basic/simple strafe: facing does not change
    assert play._lead(10.4, {"bearing": 30.0}, [0, 0, 0], "basic")["turn"] == 0
    # no focus: held turn button
    assert play._lead(6.0, None, [1, 0, 0], "defend")["turn"] == -1
    assert play._lead(6.0, None, [0, 1, 0], "defend")["turn"] == 1
    # corridor: held TURN_LEFT/RIGHT at idx 4/5
    assert play._lead(6.0, None, [0, 0, 0, 0, 0, 1, 0, 0, 0], "corridor")["turn"] == 1
