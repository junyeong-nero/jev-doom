# Context injection + latency-gap tracking — design

Date: 2026-09-20. Branch: `context-injection-tracking`.

## Why

Reference repos studied: `junyeong-nero/jev-ultrafast` (indexed action
space, speculative fan-out, structured rules, `recent_actions`,
`validate_choice`) and `hosseintoussi/jev-flappy-bird` (latency lead,
vocabulary matching, words next to numbers, math in code).

Measured gap on `defend` (seeds 1–5): Jev 2.80±1.33 / 3.60±0.80 kills vs
the no-API heuristic 13–18 kills. The heuristic re-aims every 4 tics
(~9 Hz); the Jev loop decides at ~2.5 Hz (≈290 ms) and spends the
latency window blindly repeating the last button vector. The binding
constraint is decision granularity, not prompt wording. Fix: Jev picks
*which enemy* and *whether to shoot* (~3 Hz); code keeps that target
centered at 4-tic cadence in between, and the snapshot Jev decides on is
predicted forward to when the answer lands.

## Scope

Four changes, defend-family first (defend/basic/simple), corridor gets
the structural pieces (structured instructions, target head, validation,
lead) but keeps its `_extend` loop.

### A. Snapshot (`doom/encoder.py`)

- Each enemy gains `idx` (1..N, assigned after the visible-first/closest
  sort and the `MAX_ENEMIES` cap) and `side`, a word bucket in exactly
  the vocabulary the criteria use:
  `centered` (|bearing| ≤ `CENTER_DEGREES`), `left`/`right` (≤ 45°),
  `far_left`/`far_right` (otherwise). Numeric fields stay.
- `recent`: list of up to 5 most-recent decision outcomes
  `{action, hp_change, ammo_used, kills_change}`, oldest first. `last`
  is kept (readers, `_kite_ok`).
- Latency lead: new keyword `lead: dict | None`,
  `{"tics": int, "turn": -1|0|+1}` where `turn=+1` is TURN_RIGHT.
  Predicted angle = `angle − TURN_DEG_PER_TIC × tics × turn`: bearing is
  `angle − abs_deg` (right-positive), so turning right lowers `angle`
  and pulls a right-side target toward 0. Enemy positions are extrapolated by
  `velocity × tics` before bearing/dist. Snapshot exposes `lead_tics`.
  `lead=None` → identical output to today (log/replay compat).

### B. Questions (`doom/config.py`, `doom/policy.py`)

- `policy.build_questions(snapshot, scenario)` builds the question dict
  per snapshot. `config` holds rule text and static criteria only.
- Instructions become objects: `{"goal": str, "rules": [str, ...]}`;
  `RULES_COMMON` (coordinate conventions, vocabulary) is shared by every
  question in the request.
- defend-family: `aim` is removed. New `target` Choice whose criteria are
  built from `snapshot["enemies"]`: key = `str(idx)`, value =
  `{"type", "side", "range", "visible", "closing"}`; plus `"none"` (no
  enemy worth engaging). `fire` stays a Choice(shoot/hold) but its
  criteria say "an enemy with `side` = `centered` and `visible` = true".
  `danger` Score unchanged.
- corridor: `action` Choice and `danger` unchanged in content, only
  wrapped into `{"goal","rules"}`; a `target` head is added to the same
  request and consumed only when the chosen action is one of
  `turn_left/turn_right/attack/strafe_left_fire/strafe_right_fire` (turn
  hold = proportional to the target's bearing, capped at
  `TURN_TICS_MAX`). Other actions ignore it.
- `validate_choice(answer, ids)` mirrors ultrafast: choice ∈ ids,
  probabilities keyed by ids, finite, sum≈1, argmax consistent. Invalid →
  returns None; `to_action` treats it as low confidence (defend: scan
  turn; corridor: existing fallback advance). Never raises.
- `to_action` (defend): target idx → that enemy's bearing → proportional
  turn tics (`TURN_TICS_MIN..MAX`) toward it; `none`/invalid → scan.
  Fire path unchanged (shoot pick + ammo + chainable burst logic), but
  `_chainable_target` uses the picked target when present.
- `_format` `{ammo}` substitution keeps working on the `goal` string.

### C. Latency-gap tracking (`doom/play.py`)

- 3-button scenarios only: replace `_extend` with `_track(game, focus_id,
  fire_ok, frames, done)`. Every 2-tic chunk while `not done()`:
  read `game.get_state()`, find the focus object by id in `objects`
  (+ label visibility), compute bearing with the encoder's `_rel`.
  - |bearing| ≤ `CENTER_DEGREES` and visible and `fire_ok` and ammo>0:
    press ATTACK `FIRE_TICS`, release `RELEASE_TICS`.
  - else if |bearing| > `CENTER_DEGREES`: turn toward it for
    `min(TURN_TICS_MAX, max(TURN_TICS_MIN, round(|b|/TURN_DEG_PER_TIC)))`
    tics in 2-tic chunks.
  - else hold zeros 2 tics.
  - No focus / focus gone: fall back to holding `cur` (today's behavior).
  `fire_ok` = the previous Jev answer's fire choice was `shoot`. Jev
  authorizes fire on this target; code only keeps aim and repeats it.
- corridor: `_extend` unchanged.
- Lead: count tics stepped during the last `_track`/`_extend`
  (`gap_tics`), EMA with α=0.2, `lead = {"tics": round(ema), "turn":
  ±1 if tracking is turning toward focus else sign of held turn}`.
  Passed to `encode` in `fresh()`. Logged per decision as `lead_tics`.
- Reads of `objects` in `_track` cost one `get_state()` per 2-tic chunk;
  acceptable (sync mode already renders each chunk).

### D. Verification

- `tests/test_encoder.py` (offline, synthetic `state` objects): idx/side
  assignment, `recent` window, lead extrapolation (enemy moved by
  velocity×tics; angle shift direction), `lead=None` parity.
- `tests/test_policy.py`: `build_questions` shapes (defend has
  target/fire/danger and criteria keys match enemy idx + none; corridor
  has action/target/danger), `validate_choice` accept/reject cases,
  `to_action` target→turn direction/tics, invalid answer → scan, fire
  gating unchanged.
- `tests/test_track.py`: `_track` with a fake game (scripted objects):
  turns toward off-center focus, fires only when `fire_ok`, falls back to
  `cur` without focus.
- Live: `uv run python -m doom.play --scenario defend --suite` before
  (baseline already in README) and after; report mean±std and shots/ep;
  one corridor suite to confirm no regression. Update README results and
  action-space tables, AGENTS.md async-loop note.

## Non-goals

- Corridor tracking during the gap (movement/turn conflict) — later.
- Prompt token diet for corridor prose — after results.
- Removing code-side reflexes (dodge/cover/switch) — repo direction is
  Jev=what, code=how.
