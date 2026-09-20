import math

from doom import policy
from doom.encoder import encode
from tests.conftest import PLAYER, VARS, label, obj, state


def snap_two():
    st = state([PLAYER,
                obj(1, "Zombieman", 100.0, -50.0),   # +26.6 right visible
                obj(2, "Demon", 0.0, 100.0)],         # -90 far_left
               labels=[label(1)])
    return encode(st, VARS)


def test_validate_choice_accepts_wellformed():
    ans = {"choice": "1", "confidence": 0.9,
           "probabilities": {"1": 0.7, "2": 0.2, "none": 0.1}}
    assert policy.validate_choice(ans, ["1", "2", "none"]) is ans


def test_validate_choice_rejects_bad_shapes():
    ids = ["1", "2", "none"]
    assert policy.validate_choice(None, ids) is None
    assert policy.validate_choice({"choice": "9", "confidence": 0.5,
                                  "probabilities": {"1": .5, "2": .3, "none": .2}}, ids) is None
    assert policy.validate_choice({"choice": "1", "confidence": 0.5,
                                  "probabilities": {"1": .5, "2": .3}}, ids) is None
    assert policy.validate_choice({"choice": "1", "confidence": 0.5,
                                  "probabilities": {"1": .5, "2": .5, "none": .5}}, ids) is None
    assert policy.validate_choice({"choice": "2", "confidence": 0.5,
                                  "probabilities": {"1": .6, "2": .3, "none": .1}}, ids) is None
    assert policy.validate_choice({"choice": "1", "confidence": math.nan,
                                  "probabilities": {"1": .6, "2": .3, "none": .1}}, ids) is None
    # legacy answers without probabilities are tolerated (older API shapes)
    assert policy.validate_choice({"choice": "1", "confidence": 0.8}, ids) is not None


def test_build_questions_defend_shapes():
    snap = snap_two()
    q = policy.build_questions(snap, "defend")
    assert set(q) == {"target", "fire", "danger"}
    assert list(q["target"]["criteria"]) == ["1", "2", "none"]
    c1 = q["target"]["criteria"]["1"]
    assert c1 == {"type": "Zombieman", "side": "right", "range": "close",
                  "visible": True, "closing": False}
    assert q["target"]["instructions"]["rules"] is q["fire"]["instructions"]["rules"]
    assert "centered" in q["fire"]["criteria"]["shoot"]
    assert "26 bullets" in q["fire"]["instructions"]["goal"]
    assert q["danger"]["type"] == "score"


def test_build_questions_corridor_keeps_action_adds_target():
    snap = snap_two()
    q = policy.build_questions(snap, "corridor")
    assert set(q) == {"action", "target", "danger"}
    assert "advance" in q["action"]["criteria"]
    assert "TARGET LOCK" in q["action"]["instructions"]["goal"]
    assert list(q["target"]["criteria"]) == ["1", "2", "none"]


def test_target_ids_empty_list_is_none_only():
    snap = encode(state([PLAYER]), VARS)
    assert policy.target_ids(snap) == ["none"]
