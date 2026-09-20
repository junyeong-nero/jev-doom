# AGENTS.md — jev-doom contributor guide

Jev (TypeSafe API) plays VizDoom defend_the_center: 1-flight async
encode → decide → act. A geometry-only heuristic brain is kept as a
no-API baseline. Read `README.md` for results; this file is for changing
the code without breaking it.

## Setup

```sh
uv sync
cp .env.example .env   # then set TYPESAFE_API_KEY (jev brain only)
uv run python -m doom.play --brain jev --seed 1
uv run python -m doom.play --brain heuristic --suite  # no API key needed
uv run pytest   # offline unit tests (synthetic states, no engine)
```

- `uv run` everywhere (uv-managed Python; system Python has broken SSL certs).
- One scenario (`defend`), two brains (`jev`, `heuristic`).

## Entry points

| Command | What |
|---|---|
| `doom.play --brain jev\|heuristic [--suite\|--seed N] [--episodes N] [--record] [--timeout N] [--visible]` | episodes → `runs/*.jsonl` + `.summary.json` |
| `doom.human [--episodes N]` | keyboard play (pygame window, arrows + space) |
| `doom.compare` | scoreboard incl. seeded mean/std |

## Module map

- `doom/doom_env.py` — game factory. Single-entry scenario table owns
  buttons, game variables, skill. **Order matters, see below.**
- `doom/encoder.py` — `encode(state, game_vars, ...)` → snapshot dict
  (enemies, sectors, player).
- `doom/policy.py` — Jev policy: `build_questions` / `decide` /
  `to_action` (indexed target + shoot/hold + danger → 3-button vector).
  `reset_episode()` must reset ALL module-global state (scan turn,
  fire streak, threat memory, turn counts).
- `doom/heuristic.py` — no-API baseline: `heuristic_action(snapshot)` →
  3-button vector + hold tics + reason. Nearest-visible aim, ±8° fire
  rule, 4-tic re-aim cadence. `danger_of()` feeds the logs.
- `doom/config.py` — Jev questions, thresholds, tic constants.
- `doom/play.py` — 1-flight async episode loop (Jev) + synchronous
  baseline loop, press+release firing, logging, seeds, recording.
- `tests/` — offline pytest suite; `conftest.py` has the synthetic
  state/label/object builders and a `FakeGame`.
- `doom/compare.py`, `doom/human.py` — scoreboard, keyboard play.

## Hard-won conventions (read before editing)

**Coordinates.** Bearings are degrees, **right-positive** (Doom is flipped
vs `atan2`: screen-right is negative atan2 direction). Verified against
label screen-x, not by reasoning. `Object.id` ≠ `Label.object_id` (different
structs); match objects to labels via `label.object_id == object.id`.

**Game variables are positional.** Defend order is
`[HEALTH, AMMO2, KILLCOUNT, ANGLE, HITS_TAKEN, DAMAGECOUNT]`. Appending
anywhere but the end silently misreads everything downstream.

**Button vectors are positional.** Defend: 3
`[TURN_LEFT, TURN_RIGHT, ATTACK idx 2]`. `ATTACK_IDX` must stay valid
everywhere (play, human, policy).

**Pistol is semi-auto.** Every shot needs press + release tics or holds go
silent. Turn rate is ~0.44°/tic. Standard cadence is 2–4 tics/hold
(turns re-aim every decision; fire-press is 2 tics + 2 release).

**Vocabulary is fixed.** Enemy `side` ∈ `far_left|left|centered|right|
far_right` (`encoder.side_of`). `idx` is 1-based after the
visible-first/closest sort and the `MAX_ENEMIES` cap.

**Logs are pre-action snapshots.** `runs/*.jsonl` rows precede the action;
final hp/ammo/kills come from game variables into `.summary.json`.
Filenames: `..._ep<N>[_seed<S>][_heuristic].jsonl`; compare.py keys off these.

**Eval discipline.** `game.set_seed()` before `new_episode`; suite is seeds
1–5. The *game* is deterministic per seed — always report suite mean/std,
never single runs. *Jev* is not — same seed twice can differ, so compare
suite mean/std.

## Git rules

- Never commit `runs/` or worktree leftovers (all gitignored — verify
  with `git status` before pushing).
- One issue → one branch → one PR with `Closes #N`.
- Docs that must stay true: README results tables and the action-space
  table when buttons change.
