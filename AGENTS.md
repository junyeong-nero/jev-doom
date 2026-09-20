# AGENTS.md — jev-doom contributor guide

VizDoom + TypeSafe Jev (System One model). The game loop runs in code; Jev
makes one structured decision per tick from a JSON snapshot — no pixels,
no chat. Read `README.md` for results; this file is for changing the code
without breaking it.

## Setup

```sh
uv sync
# .env holds JEV_APIKEY=... (or TYPESAFE_API_KEY). NEVER commit it.
uv run python -m doom.play --scenario defend --brain heuristic --seed 1
uv run pytest   # offline unit tests (synthetic states, no engine, no API)
```

- `uv run` everywhere (uv-managed Python; system Python has broken SSL certs).
- Heuristic brain needs no API key; Jev brain does.
- Key env names: `TYPESAFE_API_KEY` first, `JEV_APIKEY` fallback (`doom/config.py`).

## Entry points

| Command | What |
|---|---|
| `doom.play --scenario {defend,basic,simple,corridor} [--brain {jev,heuristic}] [--seed N\|--suite] [--record] [--timeout N]` | episodes → `runs/*.jsonl` + `.summary.json` |
| `doom.human --scenario ...` | keyboard baseline (pygame window) |
| `doom.compare [--scenario ...]` | scoreboard incl. seeded mean/std |

Scenarios: `defend` (turn-only arena), `basic`/`simple` (strafe + shoot one
monster), `corridor` = deadly_corridor skill 5 (full movement, 6 shooters).

## Module map

- `doom/doom_env.py` — game factory. Scenario table owns buttons, game
  variables, skill, depth flag. **Order matters, see below.**
- `doom/encoder.py` — `encode(state, game_vars, last, focus)` → snapshot JSON.
- `doom/policy.py` — `build_questions()` (request built per snapshot:
  `target` criteria keyed by enemy `idx`, shared `rules`), `decide()` (one
  Jev call), `validate_choice()`/`picked_target()` (fail-closed answer
  checks), `to_action()` (answers → button vector) + code-side reflexes
  (dodge, cover, switch, kite, scan).
- `doom/config.py` — rule text (`RULES_COMMON`, `*_GOAL`), static criteria,
  thresholds, action maps, tic constants.
- `doom/play.py` — episode loops (async 1-flight Jev loop + sync heuristic
  loop), `_track` (latency gap spent re-aiming at the picked target,
  3-button scenarios) / `_extend` (corridor hold), lead EMA, logging,
  seeds, recording.
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
SELECTED_WEAPON, SELECTED_WEAPON_AMMO, WEAPON3, AMMO1]`; other scenarios
stop after DAMAGECOUNT. Appending anywhere but the end silently misreads
everything downstream (once shipped shotgun logic reading hit counters).

**Button vectors are positional.** defend/basic/simple: 3
`[LEFT/RIGHT-or-TURN, ..., ATTACK idx 2]`. Corridor: 9
`[FWD, BACK, LEFT, RIGHT, TLEFT, TRIGHT, ATTACK idx 6, SELECT_WEAPON3, SPEED]`.
Append only; `ATTACK_IDX` must stay valid everywhere (play, human, policy).

**Pistol is semi-auto.** Every shot needs press + release tics or holds go
silent. Turn rate is ~0.44°/tic. Standard cadence is 4 tics/hold everywhere
(TURN/MOVE/fire-press); sustained-fire bursts cap at 12.

**Vocabulary is fixed.** Enemy `side` ∈ `far_left|left|centered|right|
far_right` (`encoder.side_of`), and every criterion that mentions a
bucket must use that exact token (jev-flappy-bird: mismatched words cost
a lot). `idx` is 1-based after the visible-first/closest sort and the
`MAX_ENEMIES` cap; `target` criteria keys are `str(idx)` + `none`.

**Threats vs pickups.** Labels carry `object_category` (`Monster` vs
Armor/Weapon/…): visible non-monsters leave the enemy list. Off-screen
items fall back to name matching. `MarineChainsawVzd` are REAL attackers
(verified: they advance and kill an idle player) — never filter them.

**Logs are pre-action snapshots.** `runs/*.jsonl` rows precede the action;
final hp/ammo/kills come from game variables into `.summary.json`. Log rows
carry both fire shapes (legacy `noul` + Choice) — keep readers defensive.
Filenames: `..._ep<N>[_seed<S>][_heuristic].jsonl`; compare.py keys off these.

**Eval discipline.** `game.set_seed()` before `new_episode`; suite is seeds
1–5. The *game* is deterministic per seed; *Jev* is not — always report
suite mean/std, never single runs. `basic` ends on kill (short episodes);
`defend`/`corridor` end on death/timeout. death_penalty is unset, so defend
reward == kills.

**Async loop.** 1-flight: one `decide()` in a background thread. While it
runs, 3-button scenarios `_track` the picked target (turn toward it in
2–4 tic holds; fire press/release only if the last fire answer was
`shoot`); corridor `_extend`s the last safe action (2-tic chunks,
real-time paced). Both return the tics they stepped → `gap_ema` →
`encode(lead=...)`: the snapshot Jev sees is dead-reckoned that far
ahead (enemy velocity, player turn). `focus` follows Jev's `target` pick.
`cur`/`cycle` track the held action; `reset_episode()` must reset ALL
module-global policy state (focus, dodge side, streaks, counters).

## Git rules

- Never commit `.env`, `runs/`, worktree leftovers (all gitignored — verify
  with `git status` and `git ls-files | grep env` before pushing).
- One issue → one branch → one PR with `Closes #N`. Merge conflicts
  concentrate in `policy.py` (defend branch), `config.py` (questions), and
  `play.py` (loop); resolve by keeping both behaviors and re-verifying with
  a live seeded episode, not by picking sides.
- Docs that must stay true: README results tables, cost numbers, and the
  action-space tables when buttons/questions change.
