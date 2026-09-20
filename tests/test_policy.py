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


def answers(target="1", fire="hold", danger=0.5, conf=0.9):
    return {"target": {"choice": target, "confidence": conf},
            "fire": {"choice": fire, "confidence": 0.9},
            "danger": {"score": danger}}


def setup_function(_):
    policy.reset_episode()


def test_picked_target_returns_enemy_or_none():
    snap = snap_two()
    assert policy.picked_target(answers("2"), snap)["id"] == 2
    assert policy.picked_target(answers("none"), snap) is None
    assert policy.picked_target(answers("42"), snap) is None
    assert policy.picked_target({}, snap) is None


def test_to_action_turns_toward_picked_target_proportionally():
    snap = snap_two()  # idx1 bearing +26.6 (right), idx2 -90 (far_left)
    vec, tics, reason = policy.to_action(answers("1"), snap, scenario="defend")
    assert vec == [0, 1, 0] and tics == 4  # round(26.6/0.44)=60 -> cap 4
    vec, tics, _ = policy.to_action(answers("2"), snap, scenario="defend")
    assert vec == [1, 0, 0] and tics == 4


def test_to_action_small_bearing_uses_min_tics_and_centered_holds():
    st = state([PLAYER, obj(1, "Zombieman", 100.0, -2.0)],  # +1.1 centered
               labels=[label(1)])
    snap = encode(st, VARS)
    vec, tics, reason = policy.to_action(answers("1"), snap, scenario="defend")
    assert vec == [0, 0, 0] and "center" in reason
    st = state([PLAYER, obj(1, "Zombieman", 100.0, -22.0)],  # +12.4 -> 28 tics -> cap 4
               labels=[label(1)])
    snap = encode(st, VARS)
    vec, tics, _ = policy.to_action(answers("1"), snap, scenario="defend")
    assert vec == [0, 1, 0] and tics == 4


def test_to_action_fires_when_shoot_and_ammo():
    snap = snap_two()
    vec, tics, _ = policy.to_action(answers("1", fire="shoot"), snap,
                                    scenario="defend")
    assert vec == [0, 0, 1]
    snap["player"]["ammo"] = 0
    vec, _, _ = policy.to_action(answers("1", fire="shoot"), snap,
                                 scenario="defend")
    assert vec != [0, 0, 1]


def test_to_action_none_or_invalid_scans():
    snap = snap_two()
    vec1, _, r1 = policy.to_action(answers("none"), snap, scenario="defend")
    vec2, _, r2 = policy.to_action(answers("bogus"), snap, scenario="defend")
    assert vec1 in ([1, 0, 0], [0, 1, 0]) and "scan" in r1
    assert vec2 in ([1, 0, 0], [0, 1, 0]) and vec2 != vec1 and "scan" in r2


def test_corridor_turn_uses_target_bearing_when_same_side():
    snap = snap_two()
    snap["path"] = {"left": "wall", "center": "open", "right": "wall"}
    policy._corridor_decisions = policy.OPENING_SPRINT  # skip the sprint
    ans = {"action": {"choice": "turn_right", "confidence": 0.9},
           "target": {"choice": "1", "confidence": 0.9},  # +26.6 right
           "danger": {"score": 0.2}}
    vec, tics, reason = policy.to_action(ans, snap, scenario="corridor")
    assert vec[5] == 1 and tics == 4 and "b=26.6" in reason
    policy._corridor_decisions = policy.OPENING_SPRINT
    ans["target"]["choice"] = "2"  # far_left: disagrees with turn_right
    vec, tics, reason = policy.to_action(ans, snap, scenario="corridor")
    assert vec[5] == 1 and tics == policy.C.TURN_TICS and "b=" not in reason
