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
