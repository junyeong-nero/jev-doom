# jev-doom — Doom played by Jev

VizDoom + TypeSafe's Jev (a System One model). The game loop runs in code;
Jev makes one structured decision per tick — no text, no parsing, just typed
answers with probabilities. Jev never sees pixels: object positions, angles,
and a depth-derived wall map are encoded to JSON and sent as state.

## Gameplay (deadly_corridor, skill 5)

![Jev playing](assets/corridor_ep.gif)

Full quality: [assets/corridor_ep.mp4](assets/corridor_ep.mp4)
(a frame every 2 game-tics, 8fps — the full episode is ~100 game-tics,
about 3 seconds of Doom time; skill-5 corridor ends runs fast)

## Run it

```sh
uv sync
# .env holds JEV_APIKEY=... (or TYPESAFE_API_KEY)
uv run python -m doom.play --scenario corridor --episodes 2
uv run python -m doom.play --scenario corridor --record  # also saves frames
uv run python -m doom.human --scenario defend  # keyboard baseline
uv run python -m doom.compare                  # scoreboard
```

## What Jev actually sees (context injection)

No pixels, no history — one JSON snapshot per decision. Example (real,
deadly_corridor spawn):

```json
{
  "player": {"health": 100, "ammo": 52, "kills": 0, "angle": 0, "pos": [0, 0]},
  "enemies": [
    {"type": "ShotgunGuy", "bearing": -21.8, "dist": 172,
     "range": "close", "visible": true, "closing": false},
    {"type": "Zombieman", "bearing": 21.8, "dist": 172,
     "range": "close", "visible": true, "closing": false},
    {"type": "Zombieman", "bearing": -6.0, "dist": 611,
     "range": "mid", "visible": false, "closing": false}
  ],
  "sectors": {"left": {"enemies": 1, "nearest": "close", "visible": 1}, "...": {}},
  "center_visible": false,
  "path": {"left": "wall", "center": "open", "right": "wall"},
  "last": {"action": "strafe left + fire",
           "hp_change": -18, "ammo_used": 2, "kills_change": 0}
}
```

Field guide (also spelled out in the prompt itself): bearings in degrees,
negative = LEFT, positive = RIGHT; `closing` from radial velocity;
`path` from the depth buffer (median per screen third); `last` is
one-step feedback so the stateless model sees what its previous pick cost.
`doom/encoder.py` builds this from `objects_info` + labels + depth.
Prompt (`doom/config.py`) adds the Doom field manual: pistol ballistics,
ammo economy, monster guide (Zombieman < ShotgunGuy < ChaingunGuy),
and tactics (3+ visible = kill-zone, run; retreat is a dead-end wall).

## How it works

```
VizDoom (60fps render, sync mode)
  -> every N tics: snapshot to JSON (sectors, bearings, wall map)
  -> Jev, 1 call, 2-3 questions in parallel -> discrete action
  -> confidence gating + survival reflexes in code
```

Jev decides *what*; code handles *how long* (hold tics) and safety
overrides (dodge at critical danger, ammo gates, semi-auto release).

## Action spaces

Jev only picks from a discrete set — the surrounding code maps each pick
to a button vector held for a fixed number of tics (35 tics = 1 game second).

**defend_the_center** (3 buttons: TURN_LEFT, TURN_RIGHT, ATTACK)

| Jev question | Options | Mapping |
|---|---|---|
| aim: Choice(3) | left / center / right | turn toward most threatening sector, 16 tics (~7°) |
| fire: Noul | p ≥ 0.65 + centered + ammo | ATTACK 2 tics + 2 release (semi-auto re-press) |
| danger: Score(0–2) | — | < 0.8 conf → keep sweeping last turn direction |

**basic / simpler_basic** (3 buttons: MOVE_LEFT, MOVE_RIGHT, ATTACK)

Same 3 questions, fire threshold 0.5 (50 bullets vs 400HP needs volume).
Aim maps to strafing instead of turning; goal is to strafe until the
bearing hits 0°, then fire.

**deadly_corridor** (7 buttons: FWD, BACK, STRAFE_L, STRAFE_R, TURN_L,
TURN_R, ATTACK — skill 5, 6 armed shooters)

| Jev Choice (9) | Button vector | Hold |
|---|---|---|
| advance / retreat | FWD / BACK | 8 tics |
| strafe_left / strafe_right | strafe | 8 tics |
| turn_left / turn_right | turn | 16 tics |
| attack | ATTACK | 8 tics (~2 bullets via auto-refire) |
| strafe_left_fire / strafe_right_fire | strafe + ATTACK | 8 tics |
| danger: Score(0–2) | — | ≥ 1.7 with a stationary pick → code forces a dodge |

Corridor extras: opening 6-decision sprint (learned from a scripted rush
scoring +495), depth-buffer wall map (`path: wall/open` per sector),
monster-vs-pickup filtering via label categories.

## Results (measured 2026-09-20 from Seoul, ~250–300ms/decision)

| Scenario | Best | Notes |
|---|---|---|
| defend_the_center | **4 kills** (6 bullets, 43 decisions) | kill every episode; pistol vs demons caps survival |
| simpler_basic | **win 2/2**, untouched (hp 100) | strafe-to-center, 1–2 bullets per kill |
| deadly_corridor (skill 5) | **2 kills**, reward 1100 (seed 5; SPEED sprint) | suite mean 0.40±0.80 kills, 675±244 reward — progress, not kills, is the score |

Corridor is genuinely brutal: six hitscanners focus-firing at skill 5 melt
100hp in ~3 game-seconds in the open. Progress (not kills) is the score;
tactics that mattered: opening sprint, strafe-fire, never trading stationary.

### Seeded evaluation (fixed suite 1–5)

```sh
uv run python -m doom.play --scenario defend --suite  # seeds 1..5
uv run python -m doom.play --scenario defend --seed 1 # single seeded run
uv run python -m doom.compare                         # per-seed table + mean/std
```

`game.set_seed(seed)` is called before each `new_episode`; the seed lands in
log filenames (`..._ep0_seed1.jsonl`) and `.summary.json`. Unseeded logs
render exactly as before.

Latest defend suite (2026-09-20, merged main — aim/focus/cover/async live):

| seed | kills | bullets | acc | reward |
|---|---|---|---|---|
| 1 | 2 | 4 | 0.50 | 2 |
| 2 | 1 | 1 | 1.00 | 1 |
| 3 | 1 | 2 | 0.50 | 1 |
| 4 | 1 | 2 | 0.50 | 1 |
| 5 | 1 | 2 | 0.50 | 1 |
| mean±std | 1.20±0.40 | — | — | 1.2±0.4 |

Relaxed fire gates (Noul 0.65→0.4, center 6→10°, no settle-observe):
seeds 1–5 → kills 1,2,2,1,1 = **1.4 mean**. Barely moved — Jev's Noul
output itself caps at ~0.3–0.4 mean, so thresholds aren't the bottleneck.
Their rule-vs-intent experiment says the fix is a numeric firing rule in
the criteria, not a lower threshold. That's the next experiment.

Rule-style fire criteria (explicit "|bearing| ≤ 10 AND visible → ≥0.85,
else ≤0.15", single variable changed): seeds 1–5 → kills 2,1,1,1,2 =
**1.4 mean**. Decision-level effect is real (max output 0.79→0.95, fires
on d1) but episode scores didn't move: episodes last only 11–18 decisions
because proportional turn holds (up to the 100-tic cap) freeze the bot
while demons chew it. Next bottleneck is cadence, not firing — their bot
re-aims every 4 tics; ours holds single turns for seconds.

4-tic cadence (all holds → 4: turns capped 100→4, moves 8→4, corridor
bursts 8→4): seeds 1–5 → kills 3,2,5,2,4 = **3.20±1.17 mean**, 24–38
decisions, 7.2 shots/ep. The unlock: re-aiming every 4 tics instead of
freezing mid-turn. Still dies with ~19 bullets left — fire volume is the
next lever (their bots empty the mag; heuristic does 120+ shots/ep).

Latest corridor suite (same build; SPEED auto-run on locomotion):

| seed | kills | bullets | reward |
|---|---|---|---|
| 1 | 0 | 0 | 339 |
| 2 | 0 | 0 | 644 |
| 3 | 0 | 0 | 621 |
| 4 | 0 | 0 | 668 |
| 5 | 2 | 0 | 1100 |
| mean±std | 0.40±0.80 | — | 675±244 |

Honest footnote: the merged corridor bot currently never pulls the trigger
(0 bullets all suite) — danger never drops below the 1.7 dodge line with 6
shooters up, so every attack becomes a strafe. It scores on sprint progress
plus infighting. Teaching it to actually shoot back is open (see issues).

Caveat (verified): the *game* is deterministic under a fixed seed — scripted
actions, same seed twice → byte-identical frames + variables on defend
(60 steps) and corridor (22 steps incl. death timing); different seeds
diverge. *Jev* is not — two identical seed-1 runs gave 26 vs 28 decisions
(d1 fire 0.68 vs 0.70). Same outcome here (1 kill), but treat single runs as
samples and compare suite mean/std.

## Cost (measured, 10-decision corridor episode, v2 context)

Per decision: 1389 input / 109 output tokens, ~290ms.

| Model | $/MTok in | $/MTok out | Per episode | vs Jev |
|---|---|---|---|---|
| **Jev (measured)** | 0.042 | **free** | **$0.0006** | 1x |
| GPT-5.6 Luna | 0.20 | 1.20 | $0.0041 | 7x |
| GPT-5.4 Nano | 0.20 | 1.25 | $0.0041 | 7x |
| Claude Haiku 4.5 | 1.00 | 5.00 | $0.0193 | 33x |
| GPT-5.6 Terra (same intelligence tier) | 2.00 | 12.00 | $0.0409 | 70x |

At ~3.5 decisions/sec for an hour: Jev **$0.73** vs Terra $51.
(Richer v2 context nearly doubled input tokens vs v1 — still 70x cheaper.)
LLM side assumes minimal structured JSON output (a lower bound — real
reasoning traces cost more and answer in seconds, not 250ms).
Rates: TypeSafe blog (Jev), OpenAI/Anthropic public pricing (Sep 2026).

## Human vs Jev vs heuristic

Same maps, same rules, winner takes the scoreboard:

```sh
uv run python -m doom.human --scenario defend  # arrows + space
uv run python -m doom.human --scenario corridor  # WASD + arrows + space
uv run python -m doom.play --scenario defend --brain heuristic --suite  # no-API baseline
uv run python -m doom.compare
```

Score = kills / bullets / accuracy. defend suite (seeds 1–5):
**heuristic 14.8** (13–18) vs **Jev 1.2–1.4**. The baseline sprays all 26
bullets (±8° rule, 4-tic cadence); Jev dies with 90% ammo unspent.
Fire volume wins — which is exactly what the fire-gate experiment below tests.

### External comparison: tirukovelamanoj/jev-plays-doom

Same wad, same skill/timeout, their Jev scores **6.55**/ep (20 eps, seed 1234,
metric = total_reward). Same-seed check with our heuristic: **9** vs their
6.55 heuristic-mean — same ballpark, no 3x gap; our 13–18s are seed luck plus
full-mag dumping. Methodology deltas that matter: their fire rule is a single
numeric Choice (±8°, shoot the moment aligned); ours gates through aim Choice
\+ fire Noul + confidence. Their "decoration" filter on MarineChainsawVzd we
reject: idling proves marines advance and kill you (100→dead in ~10 iters),
so our KILLCOUNT stands — but note their kills skew toward demons, ours
include the 1-bullet marines. Apples-to-apples needs the same target set;
that plus a shared seed is the next comparison upgrade.

## Things learned (the hard way)

- Pistol is semi-auto: every shot needs a release tic, or holds go silent.
- Turn rate is ~0.44°/tic — turn holds must be long, with anti-jitter sweep.
- Doom's angle convention is flipped vs atan2: screen-right is negative.
  Verified against the label buffer's screen-x, not by reasoning.
- VizDoom `Object` uses `.id`, `Label` uses `.object_id`. Different structs.
- Label `object_category` (Monster vs Armor/...) filters pickups out of
  the threat list — a GreenArmor once counted as a "close enemy".
- basic-family monsters are glass (1–2 bullets); a +101 "mystery reward"
  turned out to be a kill bonus, not a bug.
- `runs/` logs are pre-action snapshots; final hp/ammo/kills come from
  game variables and are saved to `.summary.json`.
- State engineering beat prompt tweaks: per-enemy type/bearing/closing
  (v2) scores kills where sector buckets (v1) went 0-7.
- A stateless model can still use feedback: one-step `last` (hp/ammo/kill
  deltas) lets Jev react to its own mistakes next decision.
