# jev-doom — deadly_corridor heuristic bot

VizDoom, skill 5, six armed shooters. One supported scenario
(`deadly_corridor`), one supported brain: a geometry-only heuristic —
no API key, no network, no model. Every iteration encodes the live game
state to a snapshot (object positions, bearings, depth-derived wall map)
and acts on it immediately.

## Gameplay (deadly_corridor, skill 5)

<p align="center">
  <img src="assets/corridor_ep.gif" alt="Heuristic bot playing deadly_corridor" width="480" />
  <br />
  <sub>Opening sprint out of the spawn kill-zone, then strafe-fire down the hallway — full episode is ~100 game-tics, about 3 seconds of Doom time; skill-5 corridor ends runs fast. Full quality: <a href="assets/corridor_ep.mp4">corridor_ep.mp4</a> (a frame every 2 game-tics, 8fps).</sub>
</p>

## Run it (copy-paste, no API key)

```sh
uv sync
```

```sh
# 1) Watch it play once
uv run python -m doom.play --seed 1 --visible

# 2) Full seeded suite + scoreboard (seeds 1..5, the numbers we report)
uv run python -m doom.play --suite
uv run python -m doom.compare

# extras
uv run python -m doom.play --record      # also saves frames
uv run python -m doom.human              # WASD + arrows + space, you play it
uv run pytest   # offline unit tests (synthetic states, no engine)
```

Expected: episodes log to `runs/*.jsonl` (+ `.summary.json` with final
hp/ammo/kills); `compare` prints a per-seed table with mean/std.

## How the loop runs

Synchronous: encode → decide → act, every few game-tics. No waiting,
no threads, no latency gap to cover.

```
┌─────────────────────────────────────────────────────────────────┐
│  PER-DECISION PIPELINE  (encode → act, ~4 tics each)            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  VizDoom state                                                  │
│    objects + labels + depth buffer + game vars                  │
│      │                                                          │
│      ▼                                                          │
│  encode()  →  snapshot                                          │
│    enemies (idx, type, bearing, side, dist, range,              │
│              visible, closing), sectors, center_visible,        │
│    path (wall/open per sector, from depth), player              │
│    (hp, ammo, kills, shotgun, shells, hits_taken)               │
│      │                                                          │
│      ▼                                                          │
│  heuristic_action()  →  9-button vector + hold tics             │
│    1. opening sprint:  first 6 decisions always advance         │
│    2. cover reflex:    hit from off-screen → strafe to wall    │
│                        or face last-seen threat sector          │
│    3. bigger gun:      shotgun owned + shells → switch now      │
│    4. aim:             nearest visible shooter;                 │
│                        |bearing| ≤ 8° + ammo → fire,            │
│                        else turn toward it (4-tic holds)        │
│    5. dodge reflex:    danger ≥ 1.7 → firing strafe            │
│                        instead of standing still                │
│      │                                                          │
│      ▼                                                          │
│  press + release (pistol is semi-auto: every shot needs         │
│  a 2-tic press + 2-tic release or holds go silent)              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

```
TIMING (sync, ~35 tics = 1 game second)

 game tics  0    4    8    12   16   …
           │────│────│────│────│
 decide     d1   d2   d3   d4   d5   …  (each decides AND acts
 action     ├4tics┤                              immediately —
                                        no answer ever in flight)
```

Geometry aims, reflexes dodge. Turn rate is ~0.44°/tic; turns use short
4-tic holds so the next decision re-aims instead of freezing mid-turn.
Locomotion always holds SPEED (free run, no stamina). Danger is 0–2:
2.0 = close enemy visible, 1.0 = something visible, 0.0 = nothing.

## Action space (9 buttons)

[FWD, BACK, STRAFE_L, STRAFE_R, TURN_L, TURN_R, ATTACK (idx 6),
SELECT_WEAPON3, SPEED]

| Pick | Button vector | Hold |
|---|---|---|
| advance / retreat | FWD / BACK (+ SPEED) | 4 tics |
| strafe_left / strafe_right | strafe (+ SPEED) | 4 tics |
| turn_left / turn_right | turn | 4 tics |
| attack | ATTACK | 4 tics + release |
| strafe_left_fire / strafe_right_fire | strafe + ATTACK (+ SPEED) | 4 tics + release |
| switch_to_shotgun | SELECT_WEAPON3 | 6 tics |

Extras: opening 6-decision sprint (a scripted rush scored +495 vs −16
dodging in place), depth-buffer wall map (`path: wall/open` per sector),
monster-vs-pickup filtering via label categories (a GreenArmor once
counted as a "close enemy"), shotgun pickup + switch mid-episode
(ChaingunGuys/ShotgunGuys drop their guns).

## Results (deadly_corridor, skill 5, seeds 1–5, measured 2026-09-20)

| seed | kills | bullets | reward | decisions | shots |
|---|---|---|---|---|---|
| 1 | 1 | 0 | 188 | 13 | 1 |
| 2 | 0 | 3 | 202 | 15 | 8 |
| 3 | 1 | 2 | 185 | 11 | 3 |
| 4 | 1 | 4 | 181 | 18 | 9 |
| 5 | 1 | 3 | 342 | 17 | 6 |
| mean±std | 0.80±0.40 | — | 220±62 | — | — |

```sh
uv run python -m doom.play --suite  # seeds 1..5
uv run python -m doom.play --seed 1 # single seeded run
uv run python -m doom.compare       # per-seed table + mean/std
```

`game.set_seed(seed)` is called before each `new_episode`; the seed lands in
log filenames (`..._ep0_seed1_heuristic.jsonl`) and `.summary.json`.

Corridor is genuinely brutal: six hitscanners focus-firing at skill 5 melt
100hp in ~3 game-seconds in the open. Progress (not kills) is the score —
and the honest tradeoff of this bot: it shoots back (4/5 episodes land
kills, seed 1's kill is infighting) but fighting costs sprint progress,
so rewards (181–342) trail a pure no-fire sprint (~600+). Tactics that
matter: opening sprint, strafe-fire, never trading stationary.

## Human vs heuristic

Same map, same rules:

```sh
uv run python -m doom.human  # WASD move, arrows turn, Q shotgun, SHIFT run, SPACE fire
uv run python -m doom.compare
```

## Things learned (the hard way)

- Pistol is semi-auto: every shot needs a release tic, or holds go silent.
- Turn rate is ~0.44°/tic — short 4-tic holds beat long ones (re-aim early).
- Doom's angle convention is flipped vs atan2: screen-right is negative.
  Verified against the label buffer's screen-x, not by reasoning.
- VizDoom `Object` uses `.id`, `Label` uses `.object_id`. Different structs.
- Label `object_category` (Monster vs Armor/...) filters pickups out of
  the threat list — a GreenArmor once counted as a "close enemy".
- ShotgunGuys/ChaingunGuys drop their guns: pick up the shotgun mid-episode.
- `runs/` logs are pre-action snapshots; final hp/ammo/kills come from
  game variables and are saved to `.summary.json`.
- The *game* is deterministic under a fixed seed; report suite mean/std,
  never single runs.
