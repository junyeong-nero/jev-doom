"""Human vs Jev scoreboard from runs/ logs.

Usage: uv run python -m doom.compare [--scenario defend]

Seeded runs (filenames with _seed<N>, see `doom.play --suite`) get an
extra per-seed table with mean/std. Unseeded logs render exactly as before.
"""
import argparse
import glob
import json
import re
import statistics
from pathlib import Path

START_AMMO = {"defend": 26, "basic": 50, "simple": 50, "corridor": 52}

SEED_RE = re.compile(r"_seed(\d+)")


def _seed_of(path: str, summary: dict) -> int | None:
    if isinstance(summary.get("seed"), int):
        return summary["seed"]
    m = SEED_RE.search(Path(path).name)
    return int(m.group(1)) if m else None


def load_bot(path: str, scenario: str) -> dict:
    # Prefer post-episode summary (log rows are pre-action snapshots).
    sumpath = path.replace(".jsonl", ".summary.json")
    try:
        s = json.load(open(sumpath))
        kills = s.get("kills", 0)
        bullets = START_AMMO.get(scenario, 26) - s.get("ammo", 0)
        rows = [json.loads(l) for l in open(path) if l.strip()]
        ms = (round(sum(r.get("ms", 0) for r in rows) / len(rows))
              if rows else 0)
        return {"who": "jev", "kills": kills, "bullets": int(max(bullets, 0)),
                "decisions": s.get("decisions", len(rows)), "avg_ms": ms,
                "reward": s.get("reward"), "seed": _seed_of(path, s),
                "path": Path(path).name}
    except FileNotFoundError:
        pass
    rows = [json.loads(l) for l in open(path) if l.strip()]
    if not rows:
        return {}
    kills = max(r.get("kills", 0) for r in rows)
    bullets = START_AMMO.get(scenario, 26) - min(r.get("ammo", 99) for r in rows)
    return {"who": "jev", "kills": kills, "bullets": int(max(bullets, 0)),
            "decisions": len(rows),
            "avg_ms": round(sum(r.get("ms", 0) for r in rows) / len(rows)),
            "reward": None, "seed": _seed_of(path, {}),
            "path": Path(path).name + " (~kills, 구 로그)"}


def load_human(path: str, scenario: str) -> dict:
    sumpath = path.replace(".jsonl", ".summary.json")
    try:
        s = json.load(open(sumpath))
        rows = [json.loads(l) for l in open(path) if l.strip()]
        bullets = (START_AMMO.get(scenario, 26) - min(
            [r.get("ammo", 99) for r in rows] or [99])) if rows else 0
        return {"who": "human", "kills": s.get("kills", 0),
                "bullets": max(bullets, 0), "tics": s.get("tic", 0),
                "path": Path(path).name}
    except FileNotFoundError:
        return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default=None)
    args = ap.parse_args()

    for scenario in (["defend", "basic", "simple", "corridor"]
                     if args.scenario is None else [args.scenario]):
        print(f"=== {scenario} ===")
        entries = []
        for p in sorted(glob.glob(f"runs/*_{scenario}_ep*.jsonl")):
            name = Path(p).name
            if "_human_" in name:
                e = load_human(p, scenario)
            elif "_frames" in name:
                continue
            else:
                e = load_bot(p, scenario)
            if e:
                entries.append(e)
        if not entries:
            print("  (no runs yet)")
            continue
        print(f"  {'who':<6} {'kills':>5} {'bullets':>7} {'acc':>6}  note")
        for e in entries:
            acc = f"{e['kills'] / e['bullets']:.2f}" if e["bullets"] else "-"
            extra = f"{e.get('decisions', '')} decisions" if e["who"] == "jev" \
                else f"{e.get('tics', '')} tics survived"
            print(f"  {e['who']:<6} {e['kills']:>5} {e['bullets']:>7} "
                  f"{acc:>6}  {extra} ({e['path']})")
        seeded = sorted(
            (e for e in entries if e["who"] == "jev" and e.get("seed") is not None),
            key=lambda e: e["seed"],
        )
        if seeded:
            print(f"  -- seeded suite (n={len(seeded)}) --")
            print(f"  {'seed':>4} {'kills':>5} {'bullets':>7} {'acc':>6} "
                  f"{'reward':>7}  note")
            for e in seeded:
                acc = f"{e['kills'] / e['bullets']:.2f}" if e["bullets"] else "-"
                rw = f"{e['reward']:.0f}" if e.get("reward") is not None else "-"
                print(f"  {e['seed']:>4} {e['kills']:>5} {e['bullets']:>7} "
                      f"{acc:>6} {rw:>7}  ({e['path']})")
            kills = [e["kills"] for e in seeded]
            rws = [e["reward"] for e in seeded
                   if e.get("reward") is not None]
            k_mean, k_std = statistics.fmean(kills), _std(kills)
            if rws:
                r_mean, r_std = statistics.fmean(rws), _std(rws)
                print(f"  mean±std: kills {k_mean:.2f}±{k_std:.2f}, "
                      f"reward {r_mean:.0f}±{r_std:.0f} (n={len(seeded)})")
            else:
                print(f"  mean±std: kills {k_mean:.2f}±{k_std:.2f} "
                      f"(n={len(seeded)})")


def _std(xs: list) -> float:
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


if __name__ == "__main__":
    main()
