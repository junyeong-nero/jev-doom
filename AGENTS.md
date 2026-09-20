# AGENTS.md — jev-doom contributor guide

VizDoom deadly_corridor (skill 5) + a geometry-only heuristic brain. The
loop is synchronous: encode → decide → act, no API, no network, no model.
Read `README.md` for results; this file is for changing the code without
breaking it.

## Setup

```sh
uv sync
uv run python -m doom.play --seed 1
uv run pytest   # offline unit tests (synthetic states, no engine)
```

- `uv run` everywhere (uv-managed Python; system Python has broken SSL certs).
- No API key, no `.env`. One scenario (`corridor`), one brain (heuristic).

## Entry points

| Command | What |
|---|---|
| `doom.play [--seed N\|--suite] [--episodes N] [--record] [--timeout N] [--visible]` | episodes → `runs/*.jsonl` + `.summary.json` |
| `doom.human [--episodes N]` | keyboard play (pygame window) |
| `doom.compare` | scoreboard incl. seeded mean/std |

## Module map

- `doom/doom_env.py` — game factory. Single-entry scenario table owns
  buttons, game variables, skill, depth flag. **Order matters, see below.**
- `doom/encoder.py` — `encode(state, game_vars, ...)` → snapshot dict
  (enemies, sectors, path, player).
- `doom/policy.py` — `heuristic_action(snapshot)` → 9-button vector +
  hold tics + reason. Nearest-visible-shooter aim, ±8° fire rule, 4-tic
  re-aim cadence + code-side reflexes (opening sprint, cover seek, dodge,
  shotgun switch). `danger_of()` feeds the dodge reflex and the logs.
  `reset_episode()` must reset ALL module-global state (decision counter,
  dodge side, threat memory, cover state).
- `doom/config.py` — thresholds, `CORRIDOR_ACTIONS` map, tic constants.
- `doom/play.py` — synchronous episode loop, press+release firing,
  logging, seeds, recording.
- `tests/` — offline pytest suite; `conftest.py` has the synthetic
  state/label/object builders and a `FakeGame`.
- `doom/compare.py`, `doom/human.py` — scoreboard, keyboard play.

## Hard-won conventions (read before editing)

**Coordinates.** Bearings are degrees, **right-positive** (Doom is flipped
vs `atan2`: screen-right is negative atan2 direction). Verified against
label screen-x, not by reasoning. `Object.id` ≠ `Label.object_id` (different
structs); match objects to labels via `label.object_id == object.id`.

**Game variables are positional.** Corridor order is
`[HEALTH, AMMO2, KILLCOUNT, ANGLE, HITS_TAKEN, DAMAGECOUNT,
SELECTED_WEAPON, SELECTED_WEAPON_AMMO, WEAPON3, AMMO1]`. Appending anywhere
but the end silently misreads everything downstream (once shipped shotgun
logic reading hit counters).

**Button vectors are positional.** Corridor: 9
`[FWD, BACK, LEFT, RIGHT, TLEFT, TRIGHT, ATTACK idx 6, SELECT_WEAPON3, SPEED]`.
Append only; `ATTACK_IDX` must stay valid everywhere (play, human, policy).

**Pistol is semi-auto.** Every shot needs press + release tics or holds go
silent. Turn rate is ~0.44°/tic. Standard cadence is 4 tics/hold everywhere
(TURN/MOVE/fire-press).

**Vocabulary is fixed.** Enemy `side` ∈ `far_left|left|centered|right|
far_right` (`encoder.side_of`). `idx` is 1-based after the
visible-first/closest sort and the `MAX_ENEMIES` cap.

**Threats vs pickups.** Labels carry `object_category` (`Monster` vs
Armor/Weapon/…): visible non-monsters leave the enemy list. Off-screen
items fall back to name matching. `MarineChainsawVzd` are REAL attackers
(verified: they advance and kill an idle player) — never filter them.

**Logs are pre-action snapshots.** `runs/*.jsonl` rows precede the action;
final hp/ammo/kills come from game variables into `.summary.json`.
Filenames: `..._ep<N>[_seed<S>]_heuristic.jsonl`; compare.py keys off these.

**Eval discipline.** `game.set_seed()` before `new_episode`; suite is seeds
1–5. The *game* is deterministic per seed — always report suite mean/std,
never single runs. Corridor ends on death/timeout.

**Reflex order in `heuristic_action`.** Opening sprint → cover/face-threat
→ shotgun switch → aim/fire → dodge. Reordering changes behavior; verify
with a live seeded episode (`--seed 1`), not by reasoning.

## Git rules

- Never commit `runs/` or worktree leftovers (all gitignored — verify
  with `git status` before pushing).
- One issue → one branch → one PR with `Closes #N`.
- Docs that must stay true: README results tables and the action-space
  table when buttons change.
