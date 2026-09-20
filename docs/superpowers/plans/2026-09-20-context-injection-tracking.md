# Context Injection + Latency-Gap Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jev picks *which enemy* (by index) and *whether to shoot* from a latency-lead-predicted snapshot; code keeps that target centered at 4-tic cadence while the next answer is in flight.

**Architecture:** `encoder.py` adds `idx`/`side` word buckets, a `recent` window and dead-reckoned lead; `policy.py` builds questions per snapshot (`build_questions`) with a dynamic `target` Choice, validates answers fail-closed, and maps the picked target to a proportional turn; `play.py` replaces the blind `_extend` hold with `_track` (turn toward the picked target; fire only if Jev's last fire answer was `shoot`) and feeds a gap-tics EMA back as `lead`.

**Tech Stack:** Python 3.12, `uv`, vizdoom 1.3, httpx, pytest (new dev dep). Tests are offline (synthetic `SimpleNamespace` states, fake game); the live check is the seeded defend suite.

**Spec:** `docs/superpowers/specs/2026-09-20-context-injection-tracking-design.md`

## Global Constraints

- Run everything with `uv run` (system Python has broken SSL certs).
- Never commit `.env` or `runs/`; check `git status` before each commit.
- Game variables and button vectors are positional (see AGENTS.md); do not reorder. `ATTACK_IDX` = 2 for defend/basic/simple, 6 for corridor.
- Bearings are degrees, right-positive: `bearing = norm180(angle − atan2deg(dy, dx))`. TURN_RIGHT *lowers* `angle`.
- Standard cadence: 4 tics per hold; `TURN_DEG_PER_TIC = 0.44`; `CENTER_DEGREES = 10`; fire press `FIRE_TICS=2` + release `RELEASE_TICS=2`.
- `reset_episode()` must reset every module-global in `policy.py`.
- `lead=None` and `recent=None` must leave `encode()` output byte-identical to today (log/replay compat).
- Corridor keeps `_extend`; only defend/basic/simple use `_track`.
- Word vocabulary is fixed: `side ∈ {far_left, left, centered, right, far_right}`; criteria text must use these exact tokens.

---

## File map

| File | Responsibility after this plan |
|---|---|
| `pyproject.toml` | adds `[dependency-groups] dev = ["pytest>=8"]` |
| `tests/conftest.py` | synthetic vizdoom-shaped state/label/object builders + fake game |
| `tests/test_encoder.py` | idx/side, recent, lead, `bearing_to`, parity |
| `tests/test_policy.py` | `validate_choice`, `build_questions`, `picked_target`, `to_action` |
| `tests/test_track.py` | `_track` behaviour against the fake game |
| `doom/encoder.py` | `encode(state, game_vars, last, focus, recent, lead)`, `side_of()`, `bearing_to()` |
| `doom/config.py` | `RULES_COMMON`, `TARGET_GOAL`, `FIRE_GOAL`, `DANGER_GOAL`, `TRACK_SCENARIOS`; corridor prose unchanged |
| `doom/policy.py` | `validate_choice`, `build_questions`, `picked_target`, `decide` (uses build_questions), `to_action` (target-driven) |
| `doom/play.py` | `_track`, gap EMA → `lead`, `recent` window, focus follows Jev's pick, new log fields |
| `README.md`, `AGENTS.md` | results + action-space tables, loop description |

---

### Task 1: Test scaffolding + enemy `idx`/`side`

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py` (empty), `tests/conftest.py`, `tests/test_encoder.py`
- Modify: `doom/encoder.py` (enemy dict construction, ~line 143; new `side_of` helper near `_bucket`)

**Interfaces:**
- Produces: `encoder.side_of(bearing: float) -> str` returning one of `far_left|left|centered|right|far_right`; every enemy dict gains `"idx": int` (1-based, after sort+cap) and `"side": str`.
- Produces (tests): `conftest.obj(id, name, x, y, vx=0.0, vy=0.0)`, `conftest.label(object_id, x=150, width=20, category="Monster")`, `conftest.state(objects, labels=(), game_vars=None)`, `conftest.VARS` = `[100.0, 26.0, 0.0, 0.0, 0.0, 0.0]` (health, ammo, kills, angle, hits, dmg).

- [ ] **Step 1: Add pytest as a dev dependency and sync**

Append to `pyproject.toml`:

```toml
[dependency-groups]
dev = ["pytest>=8"]
```

Run: `uv sync` — expected: pytest installs, `uv.lock` updates.

- [ ] **Step 2: Write conftest fakes**

`tests/__init__.py`: empty file.

`tests/conftest.py`:

```python
"""Synthetic vizdoom-shaped objects. No engine, no API."""
from types import SimpleNamespace as NS

import pytest

# [HEALTH, AMMO2, KILLCOUNT, ANGLE, HITS_TAKEN, DAMAGECOUNT]
VARS = [100.0, 26.0, 0.0, 0.0, 0.0, 0.0]


def obj(id, name, x, y, vx=0.0, vy=0.0):
    return NS(id=id, name=name, position_x=x, position_y=y,
              velocity_x=vx, velocity_y=vy)


def label(object_id, x=150, width=20, category="Monster"):
    # 320px-wide screen assumed by encoder when screen_buffer is None
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
        return [None, None, None]


@pytest.fixture
def fake_game():
    return FakeGame
```

- [ ] **Step 3: Write the failing tests for idx/side**

`tests/test_encoder.py`:

```python
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
```

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest tests/test_encoder.py -v`
Expected: FAIL — `AttributeError: module 'doom.encoder' has no attribute 'side_of'`.

- [ ] **Step 5: Implement `side_of` and tag enemies**

In `doom/encoder.py`, after `_bucket`:

```python
SIDE_WIDE_DEGREES = 45.0  # |bearing| beyond this is far_left / far_right


def side_of(bearing: float) -> str:
    """Word bucket for a bearing, in the exact vocabulary the questions use."""
    if abs(bearing) <= C.CENTER_DEGREES:
        return "centered"
    if abs(bearing) <= SIDE_WIDE_DEGREES:
        return "left" if bearing < 0 else "right"
    return "far_left" if bearing < 0 else "far_right"
```

In the enemy dict append, add `"side": side_of(bearing),` right after `"bearing"`. After the sort/cap lines:

```python
    enemies.sort(key=lambda e: (not e["visible"], e["dist"]))
    enemies = enemies[:MAX_ENEMIES]
    for i, e in enumerate(enemies, 1):
        e["idx"] = i  # question keys: Jev picks an enemy by this index
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_encoder.py -v` — expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock tests doom/encoder.py
git commit -m "feat(encoder): enemy idx + side word buckets; pytest scaffolding"
```

---

### Task 2: `recent` window, latency lead, `bearing_to`

**Files:**
- Modify: `doom/encoder.py` (`encode` signature + `_rel`; new `bearing_to`)
- Test: `tests/test_encoder.py`

**Interfaces:**
- Produces: `encode(state, game_vars, last=None, focus=None, recent=None, lead=None)`. `recent: list[dict]` is copied to `snap["recent"]` when non-empty. `lead: {"tics": int, "turn": -1|0|1, "cap_deg": float|None}` predicts angle/positions; `snap["lead_tics"]` is set when lead is given.
- Produces: `bearing_to(state, game_vars, object_id) -> tuple[float, float, bool] | None` = (bearing, dist, visible) for a live object, `None` if absent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_encoder.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_encoder.py -v`
Expected: the 4 new tests FAIL (`TypeError: encode() got an unexpected keyword argument 'recent'`, no `bearing_to`).

- [ ] **Step 3: Implement**

In `doom/encoder.py` replace `_rel` with a version that takes an optional tic offset, add `bearing_to`, and thread `recent`/`lead` through `encode`:

```python
def _rel(px: float, py: float, angle: float, o,
         tics: float = 0.0) -> tuple[float, float]:
    """Bearing (deg, right-positive) + distance from player to object.

    tics > 0 dead-reckons the object along its velocity first (latency lead).
    """
    dx = float(o.position_x) + float(o.velocity_x) * tics - px
    dy = float(o.position_y) + float(o.velocity_y) * tics - py
    dist = math.hypot(dx, dy) or 1.0
    abs_deg = math.degrees(math.atan2(dy, dx))
    # Doom: facing +X at angle 0, right hand points -Y, so screen-right
    # is negative atan2 direction -> bearing = angle - abs (right positive)
    return _norm180(angle - abs_deg), dist


def _player(state) -> tuple[float, float]:
    for o in state.objects or []:
        if o.name == "DoomPlayer":
            return float(o.position_x), float(o.position_y)
    return 0.0, 0.0


def bearing_to(state, game_vars, object_id: int):
    """(bearing, dist, visible) of a live object, or None when absent.

    Cheap per-tic read for the latency-gap tracker: no snapshot build.
    """
    angle = float(game_vars[3]) if len(game_vars) > 3 else 0.0
    px, py = _player(state)
    for o in state.objects or []:
        if o.id == object_id and o.name != "DoomPlayer":
            b, d = _rel(px, py, angle, o)
            vis = any(lb.object_id == object_id for lb in (state.labels or []))
            return round(b, 1), d, vis
    return None


def _lead_angle(angle: float, lead: dict | None) -> float:
    """Predicted facing when the answer lands (Flappy-style latency lead).

    turn=+1 is TURN_RIGHT, which LOWERS Doom's angle (bearing = angle - abs).
    cap_deg bounds the sweep (the tracker stops turning once centered).
    """
    if not lead or not lead.get("turn"):
        return angle
    delta = C.TURN_DEG_PER_TIC * float(lead["tics"])
    cap = lead.get("cap_deg")
    if cap is not None:
        delta = min(delta, float(cap))
    return angle - delta * (1 if lead["turn"] > 0 else -1)
```

In `encode`: change the signature to
`def encode(state, game_vars, last=None, focus=None, recent=None, lead=None) -> dict:`,
extend the docstring with:

```
    recent: up to 5 previous feedback dicts, oldest first (history window).

    lead: {"tics", "turn", "cap_deg"} — predict the state this many game
    tics ahead: enemies move along their velocity, the player's facing
    advances by TURN_DEG_PER_TIC*tics in the turn direction (+1 right,
    -1 left, 0 none), capped at cap_deg. None = raw current state.
```

then after `angle = ...` add:

```python
    lead_tics = float(lead["tics"]) if lead else 0.0
    angle = _lead_angle(angle, lead)
```

replace the player-position loop with `px, py = _player(state)`, change both `_rel(px, py, angle, o)` calls to `_rel(px, py, angle, o, lead_tics)`, change the `closing` block to use the led positions:

```python
        dx = float(o.position_x) + float(o.velocity_x) * lead_tics - px
        dy = float(o.position_y) + float(o.velocity_y) * lead_tics - py
```

and before `return snap`:

```python
    if recent:
        snap["recent"] = list(recent)
    if lead:
        snap["lead_tics"] = int(lead["tics"])
```

(`snap["player"]["angle"]` already rounds the led angle.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests -v` — expected: all pass (parity: the existing idx/side test still passes with no lead).

- [ ] **Step 5: Commit**

```bash
git add doom/encoder.py tests/test_encoder.py
git commit -m "feat(encoder): recent window, latency lead, bearing_to"
```

---

### Task 3: `validate_choice` + `build_questions`

**Files:**
- Modify: `doom/config.py` (add rule constants; keep `QUESTIONS`/`CORRIDOR_QUESTIONS` for the `danger` entry and corridor `action` text)
- Modify: `doom/policy.py` (`decide`, new `validate_choice`, `build_questions`)
- Create: `tests/test_policy.py`

**Interfaces:**
- Produces: `policy.validate_choice(answer: dict | None, ids: list[str]) -> dict | None` — returns the answer when valid, else `None`; never raises.
- Produces: `policy.build_questions(snapshot: dict, scenario: str) -> dict`. Defend-family keys: `target`, `fire`, `danger`. Corridor keys: `action`, `target`, `danger`. `target.criteria` keys = `[str(e["idx"]) ...] + ["none"]`.
- Produces: `policy.target_ids(snapshot) -> list[str]`.
- Produces (config): `RULES_COMMON: list[str]`, `TARGET_GOAL: str`, `FIRE_GOAL: str` (contains `{ammo}`), `DANGER_GOAL: str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_policy.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_policy.py -v` — expected: FAIL with `AttributeError ... validate_choice`.

- [ ] **Step 3: Add rule text to `config.py`**

Insert after `FOCUS_INSTRUCTIONS` (leave `FIRE_INSTRUCTIONS`, `QUESTIONS`, `CORRIDOR_QUESTIONS` in place — `danger` and the corridor `action` text are reused from them):

```python
# ultrafast-style structured instructions: every question in a request
# shares RULES_COMMON (how to read the state), plus its own goal. Words
# here MUST match the snapshot vocabulary exactly (side buckets, ranges):
# jev-flappy-bird measured a large drop when criteria and state disagreed.
RULES_COMMON = [
    "bearing: degrees, negative = LEFT, positive = RIGHT, 0 = straight ahead.",
    "side: the bearing in words: far_left, left, centered, right, far_right. "
    "centered means |bearing| <= 10.",
    "range: close (< 300 units), mid (< 700), far. dist is world units.",
    "visible: on screen right now. closing: moving toward you.",
    "idx: the enemy's key in the target question.",
    "focus: the enemy you picked last time (null when none), with its "
    "last-seen bearing. Finish it before switching unless a closer visible "
    "enemy appears.",
    "recent: your previous decisions, oldest first, with what each cost "
    "(hp_change, ammo_used, kills_change). last is the most recent one.",
    "lead_tics: the state is predicted this many game tics ahead, to the "
    "moment your answer lands.",
]

TARGET_GOAL = (
    "Pick the enemy to engage right now by its idx. Prefer close over far, "
    "visible over off-screen, centered over sides, and keep focus unless a "
    "better target appeared. Pick none only when no enemy is listed."
)

FIRE_GOAL = (
    "Decide whether to shoot RIGHT NOW. Apply this exact rule: if any enemy "
    "has side = centered AND visible = true, pick shoot; otherwise pick hold. "
    "Ammo: {ammo} bullets; every enemy needs several hits, so keep picking "
    "shoot on a centered visible enemy across consecutive decisions."
)

DANGER_GOAL = "How much danger is the player in from nearby enemies?"

# Scenarios whose latency gap is spent tracking the picked target
# (3-button layouts: left/right/attack). Corridor keeps the plain hold.
TRACK_SCENARIOS = ("defend", "basic", "simple")
```

- [ ] **Step 4: Implement `validate_choice`, `target_ids`, `build_questions`; route `decide` through it**

In `doom/policy.py` add `import math` at top and replace `_format` + `decide` with:

```python
def target_ids(snapshot: dict) -> list[str]:
    """Choice keys for the dynamic target question (enemy idx + none)."""
    return [str(e["idx"]) for e in snapshot.get("enemies", [])] + ["none"]


def validate_choice(answer, ids: list[str]):
    """ultrafast-style fail-closed check: the answer when it is a
    well-formed Choice over exactly `ids`, else None. Never raises.

    probabilities are optional (older shapes), but when present they must
    cover ids exactly, be finite in [0,1], sum to ~1 and agree with choice.
    """
    try:
        choice = answer["choice"]
        conf = answer["confidence"]
        if choice not in ids or not isinstance(conf, (int, float)):
            return None
        if not math.isfinite(conf) or not 0 <= conf <= 1:
            return None
        probs = answer.get("probabilities")
        if probs is not None:
            if set(probs) != set(ids):
                return None
            vals = list(probs.values())
            if not all(isinstance(v, (int, float)) and math.isfinite(v)
                       and 0 <= v <= 1 for v in vals):
                return None
            if abs(sum(vals) - 1) >= 0.02:
                return None
            if probs[choice] < max(vals) - 1e-6:
                return None
        return answer
    except (KeyError, TypeError, ValueError):
        return None


def _target_question(snapshot: dict) -> dict:
    criteria = {
        str(e["idx"]): {"type": e["type"], "side": e["side"],
                        "range": e["range"], "visible": e["visible"],
                        "closing": e["closing"]}
        for e in snapshot.get("enemies", [])
    }
    criteria["none"] = "No enemy listed / nothing worth engaging"
    return {"type": "choice",
            "instructions": {"goal": C.TARGET_GOAL, "rules": C.RULES_COMMON},
            "criteria": criteria}


def build_questions(snapshot: dict, scenario: str) -> dict:
    """Questions for ONE request, built per snapshot (indexed action space).

    defend-family: target (dynamic, keyed by enemy idx) + fire + danger.
    corridor: action (unchanged text, wrapped) + target + danger; the
    target head is consumed only for turn/attack picks (speculative).
    """
    ammo = int(snapshot["player"]["ammo"])
    danger = {"type": "score",
              "instructions": {"goal": C.DANGER_GOAL, "rules": C.RULES_COMMON},
              "criteria": C.QUESTIONS["danger"]["criteria"]}
    if scenario == "corridor":
        act = C.CORRIDOR_QUESTIONS["action"]
        return {
            "action": {"type": "choice",
                       "instructions": {
                           "goal": act["instructions"].format(ammo=ammo),
                           "rules": C.RULES_COMMON},
                       "criteria": act["criteria"]},
            "target": _target_question(snapshot),
            "danger": danger,
        }
    return {
        "target": _target_question(snapshot),
        "fire": {"type": "choice",
                 "instructions": {"goal": C.FIRE_GOAL.format(ammo=ammo),
                                  "rules": C.RULES_COMMON},
                 "criteria": C.QUESTIONS["fire"]["criteria"]},
        "danger": danger,
    }


def decide(client: httpx.Client, snapshot: dict,
           scenario: str = "defend") -> tuple[dict, dict, float]:
    """One system_one call. Returns (answers, usage, latency_ms)."""
    body = {"model": C.MODEL, "state": snapshot,
            "questions": build_questions(snapshot, scenario)}
    t0 = time.time()
    r = client.post(
        C.API_URL,
        headers={"Authorization": f"Bearer {C.API_KEY}"},
        json=body,
    )
    r.raise_for_status()
    data = r.json()
    return data["answers"], data.get("usage", {}), (time.time() - t0) * 1000
```

Also update `config.QUESTIONS["fire"]["criteria"]` to the state vocabulary:

```python
        "criteria": {
            "shoot": "An enemy has side = centered AND visible = true: "
                     "fire now",
            "hold": "No enemy is both centered and visible: hold fire "
                    "(aim first, save ammo)",
        },
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests -v` — expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add doom/config.py doom/policy.py tests/test_policy.py
git commit -m "feat(policy): per-snapshot indexed target question, structured rules, validate_choice"
```

---

### Task 4: `to_action` driven by the picked target

**Files:**
- Modify: `doom/policy.py` (`to_action` defend path; `_corridor_action` turn tics; new `picked_target`)
- Test: `tests/test_policy.py`

**Interfaces:**
- Produces: `policy.picked_target(answers, snapshot) -> dict | None` — the enemy dict for a valid non-`none` target answer, else `None`.
- Changes: `to_action` no longer reads `answers["aim"]`; defend-family uses `target`. Signature unchanged (`last_turn`, `after_turn` stay as ignored compat args).
- Corridor: `turn_left`/`turn_right` hold = proportional to the picked target's bearing when the target lies on that side, else `TURN_TICS`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_policy.py`:

```python
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
    assert vec[5] == 1 and tics == policy.C.TURN_TICS
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_policy.py -v` — expected: new tests FAIL (`picked_target` missing; `to_action` KeyError `'aim'`).

- [ ] **Step 3: Implement `picked_target` and rewrite the defend branch of `to_action`**

Add to `doom/policy.py` (after `validate_choice`):

```python
def picked_target(answers: dict, snapshot: dict) -> dict | None:
    """Enemy dict Jev picked in the target question, or None (none/invalid).

    Validation is fail-closed: an out-of-range idx is treated like none.
    """
    ans = validate_choice((answers or {}).get("target"), target_ids(snapshot))
    if ans is None or ans["choice"] == "none":
        return None
    idx = int(ans["choice"])
    return next((e for e in snapshot.get("enemies", []) if e["idx"] == idx),
                None)


def _turn_tics_for(bearing: float) -> int:
    """Bearing-proportional hold, clamped to [TURN_TICS_MIN, TURN_TICS_MAX]."""
    return min(C.TURN_TICS_MAX,
               max(C.TURN_TICS_MIN, round(abs(bearing) / C.TURN_DEG_PER_TIC)))
```

Replace `_chainable_target` with a target-aware version:

```python
def _chainable_target(snapshot: dict, target: dict | None) -> bool:
    """Issue-21: may a chained burst fire at this snapshot?

    Requires a centered+visible enemy at close/mid range — the picked
    target when there is one, else any. Far-range never chains.
    """
    pool = [target] if target is not None else snapshot.get("enemies", [])
    near = min(
        (e["dist"] for e in pool
         if e.get("visible") and abs(e.get("bearing", 999)) <= C.CENTER_DEGREES),
        default=None,
    )
    return near is not None and near < C.MID_DIST
```

In `to_action`, replace everything from `aim = answers["aim"]` to the end of the function with:

```python
    target = picked_target(answers, snapshot)
    fire_ans = answers.get("fire", {})
    # Issue-16: fire is a relative Choice {shoot, hold} carrying the
    # numeric rule (side = centered AND visible -> shoot). Fire iff the
    # model picks shoot: no Noul threshold, no center_visible backstop
    # (trusting the model is the experiment). Ammo gate stays.
    # Legacy Noul shape ({"noul": p}) still fires via the old threshold
    # so mid-rollout mixed answers fail safe instead of going silent.
    if "choice" in fire_ans:
        fire_choice = fire_ans["choice"]
        fire_conf = fire_ans.get("confidence")
        attack = ammo > 0 and fire_choice == "shoot"
        reason = (f"fire choice={fire_choice}"
                  + (f" conf={fire_conf:.2f}"
                     if isinstance(fire_conf, (int, float)) else ""))
    else:
        fire_p = fire_ans.get("noul", 0.0)
        threshold = C.FIRE_THRESHOLD.get(scenario, 0.65)
        attack = (
            ammo > 0
            and fire_p >= threshold
            and snapshot["center_visible"]
        )
        reason = f"fire p={fire_p:.2f} (legacy noul)"
    if attack:
        _sense(snapshot)  # keep threat memory fresh even while firing
        if _chainable_target(snapshot, target):
            # Issue-21 sustained fire: target is STILL centered+visible at
            # close/mid range, so chain the burst — longer holds on
            # consecutive shoot picks, hard-capped so one hold can never
            # freeze the bot. Under heavy fire (danger high) fire single
            # bursts only: the next decision must come fast.
            _fire_streak += 1
            danger = answers.get("danger", {}).get("score", 0.0)
            if danger >= C.BURST_DANGER_HI:
                tics = C.FIRE_TICS
            else:
                tics = min(C.FIRE_TICS
                            + C.BURST_STEP_TICS * (_fire_streak - 1),
                            C.BURST_MAX_TICS)
            return [0, 0, 1], tics, (
                f"{reason} burst x{_fire_streak} tics={tics} "
                f"danger={danger:.2f}")
        _fire_streak = 0
        return [0, 0, 1], C.FIRE_TICS, reason
    _fire_streak = 0

    # Issue-3: hit from off-screen with no strafe buttons here -> turn to
    # face the most recently visible threat sector.
    took_damage, _ = _sense(snapshot)
    if (took_damage and not snapshot["center_visible"]
            and _last_seen in ("left", "right")):
        vec = [1, 0, 0] if _last_seen == "left" else [0, 1, 0]
        _note_turn(vec)
        return vec, C.TURN_TICS, f"face threat {_last_seen} (damage, none visible)"
    _ = after_turn

    # Indexed target (ultrafast-style): Jev picked an enemy by idx; code
    # resolves the geometry — bearing-proportional turn toward it, 4-tic
    # cap so the bot re-aims every decision. Off-screen targets carry
    # bearings too (objects_info), so no blind sweep is needed.
    if target is not None:
        b = target["bearing"]
        if abs(b) <= C.CENTER_DEGREES:
            return [0, 0, 0], C.TURN_TICS, f"target {target['idx']} centered b={b}"
        vec = [1, 0, 0] if b < 0 else [0, 1, 0]
        _note_turn(vec)
        return vec, _turn_tics_for(b), (
            f"target {target['idx']} {target['side']} b={b}")

    # none / invalid: alternating scan turns instead of freezing.
    global _scan_turn
    _scan_turn = ([0, 1, 0] if _scan_turn == [1, 0, 0] else [1, 0, 0])
    side = "left" if _scan_turn == [1, 0, 0] else "right"
    _note_turn(_scan_turn)
    return _scan_turn, C.TURN_TICS, f"scan {side} (no target)"
```

Delete the now-unused `_turn_tics` and `_in_sector` helpers.

In `_corridor_action`, replace the final generic branch:

```python
    if choice in C.CORRIDOR_ACTIONS:
        vec, tics, reason = C.CORRIDOR_ACTIONS[choice]
        if conf < C.SWEEP_CONFIDENCE:
            vec, tics, _ = C.CORRIDOR_ACTIONS["advance"]
            return vec, tics, prefix + f"advance (low conf {conf:.2f})"
        # Speculative target head: for a turn, resolve the hold from the
        # picked enemy's bearing when it lies on the chosen side.
        target = picked_target(answers, snapshot)
        if choice in ("turn_left", "turn_right") and target is not None:
            b = target["bearing"]
            if (choice == "turn_left") == (b < 0):
                tics = _turn_tics_for(b)
                reason = f"{reason} -> target {target['idx']} b={b}"
        return vec, tics, prefix + reason
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests -v` — expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add doom/policy.py tests/test_policy.py
git commit -m "feat(policy): to_action follows the picked target; corridor turn uses target bearing"
```

---

### Task 5: `_track` latency-gap loop, lead EMA, recent window, log fields

**Files:**
- Modify: `doom/play.py` (`_extend` → returns tics; new `_track`, `_lead`; `run_episode` loop; log dicts)
- Create: `tests/test_track.py`

**Interfaces:**
- Produces: `play._track(game, cur, frames, done, target_id, fire_ok, game_vars_of=lambda st: list(st.game_variables)) -> int` (tics stepped). Turns toward `target_id`, fires only when `fire_ok` and centered+visible and ammo>0; falls back to `_extend(cur)` when the target is absent.
- Produces: `play._extend(...) -> int` (tics stepped; behaviour unchanged).
- Produces: `play._lead(gap_tics: float, focus: dict | None, cur: list, scenario: str) -> dict | None`.
- Log rows (defend-family, Jev): `target`, `target_conf`, `lead_tics` added; `aim`/`aim_conf` removed from Jev rows (heuristic rows unchanged). Corridor rows add `target`, `lead_tics`.

- [ ] **Step 1: Write the failing tests**

`tests/test_track.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_track.py -v` — expected: FAIL (`_track`, `_lead` missing).

- [ ] **Step 3: Implement `_extend` (return tics), `_track`, `_lead`**

In `doom/play.py` add `from .encoder import bearing_to, encode` (replace the existing encode import) and replace `_extend` with:

```python
def _extend(game, action: list, frames: list | None, done,
            cycle_attack: bool) -> int:
    """Hold the last safe action until done() is true, ~real-time paced.

    Never blocks on the API: each 2-tic chunk re-checks done() and the
    episode state. cycle_attack re-presses semi-auto fire (press/release),
    matching the sequential fire+release cadence while holding.
    Returns the number of game tics stepped (latency-gap length).
    """
    n = len(action)
    press = ([(action, C.FIRE_TICS), ([0] * n, C.RELEASE_TICS)]
             if cycle_attack else [(action, 2)])
    i, stepped = 0, 0
    while not done() and not game.is_episode_finished():
        a, t = press[i % len(press)]
        _step(game, a, t, frames, pace=True)
        stepped += t
        i += 1
    return stepped


def _track(game, cur: list, frames: list | None, done, target_id,
           fire_ok: bool) -> int:
    """Latency-gap tracker (3-button layouts): keep Jev's picked target
    centered at 4-tic cadence while the next answer is in flight.

    Jev decides WHAT (target + shoot/hold); this only resolves geometry
    per tic, like ultrafast re-reading element geometry before a click:
      - centered + visible + fire_ok + ammo -> press/release one shot
      - off-center -> turn toward it, bearing-proportional, capped 4 tics
      - centered but not allowed to fire -> hold still
    No target (none/invalid/dead) -> plain hold of the last action.
    Returns tics stepped.
    """
    if target_id is None:
        return _extend(game, cur, frames, done, False)
    n = len(cur)
    stepped = 0
    while not done() and not game.is_episode_finished():
        st = game.get_state()
        rel = bearing_to(st, list(st.game_variables), target_id) if st else None
        if rel is None:
            return stepped + _extend(game, cur, frames, done, False)
        b, _dist, vis = rel
        ammo = float(st.game_variables[1])
        if abs(b) <= C.CENTER_DEGREES:
            if fire_ok and vis and ammo > 0:
                _step(game, [0, 0, 1], C.FIRE_TICS, frames, pace=True)
                _step(game, [0] * n, C.RELEASE_TICS, frames, pace=True)
                stepped += C.FIRE_TICS + C.RELEASE_TICS
            else:
                _step(game, [0] * n, 2, frames, pace=True)
                stepped += 2
            continue
        vec = [1, 0, 0] if b < 0 else [0, 1, 0]
        tics = min(C.TURN_TICS_MAX,
                   max(C.TURN_TICS_MIN, round(abs(b) / C.TURN_DEG_PER_TIC)))
        _step(game, vec, tics, frames, pace=True)
        stepped += tics
    return stepped


def _lead(gap_tics: float, focus: dict | None, cur: list,
          scenario: str) -> dict | None:
    """Latency lead for encode(): what the gap will do to the facing.

    Tracking scenarios steer toward focus (turn = its side, capped at its
    bearing so the prediction never overshoots); basic/simple strafe, so
    facing is unchanged. Otherwise the held turn button decides.
    """
    tics = round(gap_tics)
    if tics <= 0:
        return None
    if scenario in C.TRACK_SCENARIOS and focus is not None:
        b = float(focus["bearing"])
        if scenario != "defend" or abs(b) <= C.CENTER_DEGREES:
            return {"tics": tics, "turn": 0, "cap_deg": None}
        return {"tics": tics, "turn": 1 if b > 0 else -1, "cap_deg": abs(b)}
    if scenario == "corridor":
        turn = 1 if cur[5] else -1 if cur[4] else 0
    else:
        turn = 1 if cur[1] else -1 if cur[0] else 0
    return {"tics": tics, "turn": turn, "cap_deg": None}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_track.py -v` — expected: all pass.

- [ ] **Step 5: Wire the episode loop**

In `run_episode`:

1. After `focus_prev_kills = None` add:

```python
    recent: deque = deque(maxlen=5)  # history window shown to Jev
    gap_ema = 0.0  # game tics spent per latency gap (EMA, alpha 0.2)
    fire_ok = False  # Jev's last fire answer was shoot -> tracker may fire
    track = scenario in C.TRACK_SCENARIOS
```

and `from collections import deque` at the top.

2. In `fresh()` replace the `encode(...)` line with:

```python
        lead = _lead(gap_ema, focus, cur, scenario)
        snap = encode(state, list(state.game_variables), last=prev,
                      recent=list(recent), lead=lead)
```

and add `nonlocal cur, gap_ema` is NOT needed (read-only) — leave the existing `nonlocal focus, focus_prev_kills`.

3. Replace the in-flight hold:

```python
            if not fut.done():
                # Slow API -> track the picked target (3-button) or extend
                # the last safe action (corridor), never block.
                if track:
                    gap = _track(game, cur, frames, fut.done,
                                 (focus or {}).get("id"), fire_ok)
                else:
                    gap = _extend(game, cur, frames, fut.done, cycle)
                gap_ema = gap if gap_ema == 0 else gap_ema + 0.2 * (gap - gap_ema)
                continue
```

4. After `action, tics, reason = to_action(...)` add:

```python
            tgt = picked_target(answers, snapshot)
            if tgt is not None:
                # Focus follows Jev's pick: the tracker and the next
                # snapshot's focus both mean "the enemy Jev chose".
                focus = {"id": tgt["id"], "type": tgt["type"],
                         "bearing": tgt["bearing"], "dist": tgt["dist"],
                         "engaged": engaged.get(tgt["id"], {}).get("engaged", 0),
                         "misses": 0}
            fire_ans = answers.get("fire", {})
            fire_ok = fire_ans.get("choice") == "shoot"
```

and import `picked_target` from `.policy`.

5. After `prev = {...}` is assigned, add `recent.append(prev)`.

6. Log rows. Defend-family Jev row: replace the `"aim": ...` / `"aim_conf": ...` lines with

```python
                    "target": answers.get("target", {}).get("choice"),
                    "target_conf": round(answers.get("target", {}).get("confidence", 0.0), 3),
                    "lead_tics": snapshot.get("lead_tics", 0),
```

Corridor row: add the same three keys after `"pick_conf"`.

7. The progress prints: replace `aim={answers['aim']['choice']}` with `target={answers.get('target', {}).get('choice')}`; in the corridor print add `target={answers.get('target', {}).get('choice')}` after `pick=`.

8. `_extend` in the `except` path stays as is (it now returns an int; ignore it).

- [ ] **Step 6: Smoke-run offline pieces and a short live episode**

Run: `uv run pytest tests -v` — expected: all pass.
Run: `uv run python -m doom.play --scenario defend --brain heuristic --seed 1` — expected: unchanged heuristic behaviour (13–18 kills), proves encoder parity end-to-end.
Run: `uv run python -m doom.play --scenario defend --seed 1` — expected: prints `d1: target=... fire=... [target N ... b=..]`, no tracebacks; log rows carry `target`, `lead_tics`.

- [ ] **Step 7: Commit**

```bash
git status  # no .env / runs
git add doom/play.py tests/test_track.py
git commit -m "feat(play): latency-gap target tracking, lead EMA, recent window"
```

---

### Task 6: Live evaluation + docs

**Files:**
- Modify: `README.md` (context example, defend action-space table, results), `AGENTS.md` (module map, async loop note)

**Interfaces:** none (docs).

- [ ] **Step 1: Run the defend suite**

Run: `uv run python -m doom.play --scenario defend --suite` then `uv run python -m doom.compare --scenario defend`.
Record per-seed kills, shots, decisions, avg_ms, and mean±std. Baseline to beat: 3.60±0.80 (#15–17) / 2.80±1.33 (#21); heuristic 13–18.

- [ ] **Step 2: Run one corridor suite for regression**

Run: `uv run python -m doom.play --scenario corridor --suite`. Expected: no crash; reward within the 675±244 band or better. If the `target` head breaks corridor behaviour, note it in README rather than tuning.

- [ ] **Step 3: If the defend suite did not improve, diagnose before documenting**

Read the newest `runs/*_defend_*_seed1.jsonl`: check `target` picks vs the `enemies` list in the same row, `lead_tics` (expect ~8–12), and how many rows have `fire_choice == "shoot"`. Typical fixes are vocabulary only (config text), not code; a code change here means re-running Tasks 3–5 tests and the suite.

- [ ] **Step 4: Update README**

- "What Jev actually sees": add `idx`, `side`, `recent`, `lead_tics` to the example JSON and a sentence on latency lead.
- defend action-space table: replace the `aim` row with `target: Choice(N+1)` → "turn toward the picked enemy, bearing-proportional, ≤4 tics; the latency gap tracks it (fires only after a `shoot` answer)".
- Results: add a "Indexed target + latency-gap tracking" paragraph in the same style as the existing ones with the measured suite numbers, shots/ep and lead_tics.
- Cost: note the new per-decision input tokens from `in_tok` in the logs.

- [ ] **Step 5: Update AGENTS.md**

- Module map: `doom/policy.py` — add "`build_questions()` builds the request per snapshot (target criteria = enemy idx)"; `doom/play.py` — "`_track` spends the latency gap re-aiming at the picked target (3-button), `_extend` for corridor".
- Async loop convention: add "Lead: `gap_ema` (tics) → `encode(lead=...)`; the snapshot Jev sees is predicted forward. `_track` fires only when the last fire answer was `shoot`."
- Add the fixed `side` vocabulary to the hard-won conventions ("criteria and state must use the same tokens").
- Setup: `uv run pytest` runs the offline tests.

- [ ] **Step 6: Commit**

```bash
git status  # runs/ must not be staged
git add README.md AGENTS.md
git commit -m "docs: indexed target + latency-gap tracking results"
```
