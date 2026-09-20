# jev-doom

Jev (TypeSafe's `MODEL=jev-latest`) plays VizDoom `defend_the_center`:
one structured decision per tick (indexed target + shoot/hold + danger),
no pixels — everything numeric/textual. A geometry-only `heuristic`
baseline (no API key) is kept for comparison.

```sh
uv run python -m doom.play --brain jev --suite
```

## Demo

<p align="center">
  <img src="assets/defend_ep.gif" alt="Jev playing defend_the_center" width="480" />
  <br />
  <sub>Jev brain, seed 4 re-run: 13 kills, 70 decisions — indexed target + latency-gap tracking. Full quality: <a href="assets/defend_ep.mp4">defend_ep.mp4</a> (8fps).</sub>
</p>

## Quickstart

```sh
uv sync
cp .env.example .env   # then set TYPESAFE_API_KEY (jev brain only)
```

```sh
# Jev on defend (the 10.20±2.04 setup)
uv run python -m doom.play --brain jev --suite

# Watch it play
uv run python -m doom.play --brain jev --seed 1 --visible

# No-API baseline for comparison
uv run python -m doom.play --brain heuristic --suite

# Scoreboard (seeds 1..5, the numbers we report)
uv run python -m doom.compare

# extras
uv run python -m doom.play --brain jev --record  # also saves frames
uv run python -m doom.human                      # arrows + space, you play it
uv run pytest   # offline unit tests (synthetic states, no engine)
```

Expected: episodes log to `runs/*.jsonl` (+ `.summary.json` with final
hp/ammo/kills); `compare` prints a per-seed table with mean/std.
Filenames carry brain + seed (`..._ep0_seed1.jsonl`,
`..._ep0_seed1_heuristic.jsonl`).

## How it works

Jev (`--brain jev`): 1-flight async — while the next `decide()` runs in a
background thread, code holds the last safe action; the ~10-tic latency
gap is spent tracking Jev's picked target (re-aim at 4-tic cadence,
re-fire only after a `shoot` pick) instead of repeating buttons.
Snapshots carry a latency lead (`lead_tics`, EMA of gap length):
enemies dead-reckoned along velocity, facing advanced along the held turn.

Heuristic (`--brain heuristic`): synchronous encode → decide → act, every
few game-tics. Nearest visible enemy by distance, fire within ±8°, else
turn toward it. No waiting, no threads, no latency gap to cover.

```
┌──────────────────────────────────────────────────────────────┐
│  JEV PIPELINE  (encode → decide → act, 1-flight async)       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  VizDoom state (objects + labels + game vars)                │
│    │                                                         │
│    ▼                                                         │
│  encode()  →  snapshot                                       │
│    enemies (idx, type, bearing, side, dist, range,           │
│              visible, closing), sectors, center_visible,     │
│    player (hp, ammo, kills, hits_taken), focus,              │
│    last/recent feedback, lead_tics                           │
│    │                                                         │
│    ▼                                                         │
│  decide()  →  Jev answers {target, fire, danger}             │
│    target: Choice(idx … / none) — which enemy to engage      │
│    fire:   Choice(shoot / hold) — centered AND visible rule  │
│    danger: Score(0–2)                                        │
│    │                                                         │
│    ▼                                                         │
│  to_action()  →  3-button vector + hold tics                │
│    shoot (+ammo) → ATTACK bursts (chained up to 12 tics,     │
│      single shots under heavy fire)                          │
│    indexed target → bearing-proportional turn (2–4 tics)     │
│    hit from off-screen → face last-seen threat sector        │
│    none/invalid → alternating scan                           │
│    │                                                         │
│    ▼                                                         │
│  press + release (pistol is semi-auto: every shot needs      │
│  a 2-tic press + 2-tic release or holds go silent)           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

Geometry aims, Jev picks. Turn rate is ~0.44°/tic; turns use short
2–4-tic holds so the next decision re-aims instead of freezing mid-turn.
Danger is 0–2: 2.0 = close enemy visible, 1.0 = something visible,
0.0 = nothing.

## Actions

**defend_the_center** (3 buttons: LEFT, RIGHT, ATTACK)

| Jev question | Options | Mapping |
|---|---|---|
| target: Choice(N+1), built per snapshot | enemy `idx` … / none | turn toward that enemy, bearing-proportional, 2–4 tics; latency gap tracks it; none/invalid → alternating scan |
| fire: Choice(2) | shoot / hold (rule: `side = centered` AND `visible`) | ATTACK 2 tics + 2 release; chained bursts up to 12 tics |
| danger: Score(0–2) | — | ≥ 1.7 → single bursts only |

## Results

Jev defend_the_center (indexed target + latency-gap tracking):

| seed | kills | bullets | acc | reward |
|---|---|---|---|---|
| 1 | 7 | 22 | 0.32 | 7 |
| 2 | 11 | 26 | 0.42 | 11 |
| 3 | 11 | 26 | 0.42 | 11 |
| 4 | 13 | 26 | 0.50 | 13 |
| 5 | 9 | 26 | 0.35 | 9 |
| mean±std | 10.20±2.04 | — | — | 10.2±2.0 |

Heuristic baseline (geometry only, no API — same suite, seeds 1–5):

| seed | kills | bullets | acc | decisions |
|---|---|---|---|---|
| 1 | 18 | 26 | 0.69 | 280 |
| 2 | 13 | 26 | 0.50 | 196 |
| 3 | 13 | 26 | 0.50 | 242 |
| 4 | 14 | 26 | 0.54 | 218 |
| 5 | 16 | 26 | 0.62 | 235 |
| mean±std | 14.80±1.94 | — | — | — |

```sh
uv run python -m doom.play --brain jev --suite        # seeds 1..5
uv run python -m doom.play --brain heuristic --suite  # baseline
uv run python -m doom.compare                         # per-seed table + mean/std
```

`game.set_seed(seed)` is called before each `new_episode`; the seed lands in
log filenames and `.summary.json`.

The honest baseline: the geometry bot out-kills Jev on defend (14.80 vs
10.20) — it never hesitates, never mis-picks a target, and fires the
instant something centers. Jev's cost is the experiment itself: API
latency (~10 tics per decision) plus occasional wrong picks. What Jev
buys is judgment the baseline lacks — target lock (finish one enemy
before switching), sustained chained bursts, and flinch-turns toward
off-screen shooters.

## Human

Same map, same rules:

```sh
uv run python -m doom.human    # arrows + space
uv run python -m doom.compare
```

## Notes

- Pistol is semi-auto: every shot needs a release tic, or holds go silent.
- Turn rate is ~0.44°/tic — short 2–4-tic holds beat long ones (re-aim early).
- Doom's angle convention is flipped vs atan2: screen-right is negative.
  Verified against the label buffer's screen-x, not by reasoning.
- VizDoom `Object` uses `.id`, `Label` uses `.object_id`. Different structs.
- `runs/` logs are pre-action snapshots; final hp/ammo/kills come from
  game variables and are saved to `.summary.json`.
- The *game* is deterministic under a fixed seed; report suite mean/std,
  never single runs. *Jev* is not — same seed twice can differ by a
  decision or two, so compare suite mean/std.
