from doom import encoder
from doom.encoder import encode
from tests.conftest import PLAYER, VARS, label, obj, state


def test_side_of_buckets():
    assert encoder.side_of(0) == "centered"
    assert encoder.side_of(10) == "centered"
    assert encoder.side_of(-10) == "centered"
    assert encoder.side_of(-11) == "left"
    assert encoder.side_of(45) == "right"
    assert encoder.side_of(46) == "far_right"
    assert encoder.side_of(-120) == "far_left"


def test_enemies_get_idx_and_side():
    # angle 0 faces +X; -Y is screen-right. (100,-50) -> bearing +26.6
    st = state([PLAYER,
                obj(1, "Zombieman", 100.0, -50.0),   # right, close, visible
                obj(2, "Demon", 0.0, 100.0),          # bearing -90 -> far_left
                obj(3, "Imp", 800.0, 0.0)],           # centered, far
               labels=[label(1)])
    snap = encode(st, VARS)
    e = snap["enemies"]
    # visible first, then closest: 1 (visible), 2 (dist 100), 3 (800)
    assert [x["id"] for x in e] == [1, 2, 3]
    assert [x["idx"] for x in e] == [1, 2, 3]
    assert e[0]["side"] == "right" and e[0]["bearing"] == 26.6
    assert e[1]["side"] == "far_left"
    assert e[2]["side"] == "centered"


def test_recent_window_is_copied_and_last_kept():
    st = state([PLAYER, obj(1, "Zombieman", 100.0, 0.0)])
    recent = [{"action": "aim right", "hp_change": 0, "ammo_used": 0,
               "kills_change": 0}]
    snap = encode(st, VARS, last=recent[-1], recent=recent)
    assert snap["recent"] == recent
    assert snap["last"] == recent[-1]
    assert "recent" not in encode(st, VARS)


def test_lead_extrapolates_enemy_velocity():
    # enemy at (100,0) moving -Y (toward player's right) at 10 u/tic
    st = state([PLAYER, obj(1, "Zombieman", 100.0, 0.0, vx=0.0, vy=-10.0)])
    plain = encode(st, VARS)
    led = encode(st, VARS, lead={"tics": 5, "turn": 0, "cap_deg": None})
    assert plain["enemies"][0]["bearing"] == 0.0
    assert led["enemies"][0]["bearing"] == 26.6  # atan2(50,100)
    assert led["lead_tics"] == 5
    assert "lead_tics" not in plain


def test_lead_turn_right_lowers_angle_and_caps():
    st = state([PLAYER, obj(1, "Zombieman", 100.0, -50.0)])  # bearing +26.6
    led = encode(st, VARS, lead={"tics": 10, "turn": 1, "cap_deg": None})
    assert led["enemies"][0]["bearing"] == 22.2  # 26.6 - 0.44*10
    assert led["player"]["angle"] == -4.4
    capped = encode(st, VARS, lead={"tics": 10, "turn": 1, "cap_deg": 3.0})
    assert capped["enemies"][0]["bearing"] == 23.6
    left = encode(st, VARS, lead={"tics": 10, "turn": -1, "cap_deg": None})
    assert left["enemies"][0]["bearing"] == 31.0


def test_bearing_to_live_object():
    st = state([PLAYER, obj(7, "Demon", 100.0, -50.0)], labels=[label(7)])
    b, d, vis = encoder.bearing_to(st, VARS, 7)
    assert b == 26.6 and round(d) == 112 and vis is True
    assert encoder.bearing_to(st, VARS, 99) is None
