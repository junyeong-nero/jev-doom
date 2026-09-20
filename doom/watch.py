"""Live Jev visualizer: game view + decision panel, side by side.

Wraps an episode (Jev or heuristic) in a pygame window: the left side
shows the live Doom frame with enemy boxes + picked-target highlight and
a bearing compass; the right side shows WHAT Jev sees and decides —
target/fire/danger with confidences, latency, tokens, game stats, enemy
context list, and recent decisions. Like a flappy-bird-style agent HUD.

Usage:
    uv run python -m doom.watch --brain jev --seed 1
    uv run python -m doom.watch --brain heuristic --suite
    ESC quits.
"""
import argparse
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import pygame

from . import config as C
from . import heuristic as hev
from .doom_env import make_game
from .encoder import bearing_to, encode
from .play import (SEED_SUITE, _fire_log_fields, _lead, _public_focus,
                   _update_focus)
from .policy import decide, picked_target, reset_episode, to_action

GAME_W, GAME_H = 640, 480
TOPBAR_H = 34
COMPASS_H = 30
PANEL_W = 430
WIN_W = GAME_W + PANEL_W
WIN_H = TOPBAR_H + GAME_H + COMPASS_H

BG = (13, 17, 23)
PANEL_BG = (22, 27, 34)
LINE = (48, 54, 63)
TXT = (230, 237, 243)
DIM = (139, 148, 158)
GREEN = (63, 185, 80)
AMBER = (210, 153, 34)
RED = (248, 81, 73)
BLUE = (88, 166, 255)

_fonts = {}


def _font(size: int) -> pygame.font.Font:
    if size not in _fonts:
        _fonts[size] = pygame.font.SysFont("menlo,monaco,consolas,monospace",
                                           size)
    return _fonts[size]


def _text(surf, s: str, x: int, y: int, size: int = 14,
          color=TXT) -> int:
    img = _font(size).render(s, True, color)
    surf.blit(img, (x, y))
    return img.get_width()


def _bar(surf, x: int, y: int, w: int, h: int, frac: float,
         color=BLUE) -> None:
    pygame.draw.rect(surf, LINE, (x, y, w, h), 1)
    fw = int(max(0.0, min(1.0, frac)) * (w - 2))
    if fw:
        pygame.draw.rect(surf, color, (x + 1, y + 1, fw, h - 2))


def new_hud(brain: str) -> dict:
    return {
        "brain": brain, "seed": None, "ep": 0,
        "answers": None, "snapshot": None, "labels": [],
        "frame": None, "target_id": None, "fire_ok": False,
        "fired_at": -1, "decisions": 0, "shots": 0,
        "ms_last": 0.0, "ms_avg": 0.0, "errors": 0,
        "tok_in": 0, "tok_out": 0,
        "hp": 100.0, "ammo": 26, "kills": 0,
        "focus_id": None, "lead_tics": 0,
        "enemies": [], "recent": deque(maxlen=8),
        "quit": False, "t0": time.time(),
    }


def _pump(hud: dict) -> None:
    for e in pygame.event.get():
        if e.type == pygame.QUIT:
            hud["quit"] = True
        elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            hud["quit"] = True


def _grab(game, hud: dict) -> None:
    st = game.get_state()
    if st is not None and st.screen_buffer is not None:
        hud["frame"] = st.screen_buffer.transpose(1, 2, 0)
        hud["labels"] = list(st.labels or [])


def render(screen, game_surf: pygame.Surface, hud: dict) -> None:
    screen.fill(BG)
    # --- top bar ---
    title = ("JEV IS PLAYING" if hud["brain"] == "jev"
             else "HEURISTIC BASELINE")
    _text(screen, f"● {title}", 12, 9, 15, GREEN)
    _text(screen, f"defend_the_center · seed {hud['seed']} · "
                  f"d{hud['decisions']}", 220, 10, 13, DIM)
    mm = int(time.time() - hud["t0"]) // 60
    ss = int(time.time() - hud["t0"]) % 60
    _text(screen, f"{mm:02d}:{ss:02d}", WIN_W - 60, 10, 13, DIM)

    # --- game view ---
    gx, gy = 0, TOPBAR_H
    if hud["frame"] is not None:
        pygame.surfarray.blit_array(
            game_surf, hud["frame"].swapaxes(0, 1))
    else:
        game_surf.fill((0, 0, 0))
    screen.blit(game_surf, (gx, gy))
    _draw_boxes(screen, gx, gy, hud)
    if hud["decisions"] - hud["fired_at"] <= 1 and hud["fired_at"] >= 0:
        pygame.draw.rect(screen, RED, (gx, gy, GAME_W, GAME_H), 4)
        _text(screen, "FIRE", gx + 12, gy + 10, 22, RED)

    # --- compass ---
    cy = TOPBAR_H + GAME_H
    pygame.draw.line(screen, LINE, (0, cy + COMPASS_H // 2),
                     (GAME_W, cy + COMPASS_H // 2), 1)
    pygame.draw.line(screen, DIM, (GAME_W // 2, cy + 4),
                     (GAME_W // 2, cy + COMPASS_H - 4), 1)
    for e in hud["enemies"]:
        x = int(GAME_W / 2 + max(-180.0, min(180.0, e["bearing"]))
                / 180.0 * (GAME_W / 2 - 8))
        y = cy + COMPASS_H // 2
        col = (GREEN if e["idx"] == hud["target_id"] and
               hud["target_id"] is not None else
               TXT if e["visible"] else DIM)
        r = 5 if e["idx"] == hud["target_id"] else 3
        pygame.draw.circle(screen, col, (x, y), r,
                           0 if e["visible"] else 1)
        _text(screen, str(e["idx"]), x - 4, cy + 2, 10, col)

    _draw_panel(screen, hud)
    pygame.display.flip()


def _draw_boxes(screen, gx: int, gy: int, hud: dict) -> None:
    by_id = {e["id"]: e for e in hud["enemies"]}
    for lb in hud["labels"]:
        e = by_id.get(lb.object_id)
        if e is None:
            continue  # muzzle flash / player weapon: not a tracked enemy
        x, y = int(lb.x), int(lb.y)
        w, h = int(lb.width), int(lb.height)
        if w <= 0 or h <= 0:
            continue
        is_target = (hud["target_id"] is not None
                     and e["idx"] == hud["target_id"])
        col = GREEN if is_target else AMBER
        pygame.draw.rect(screen, col, (gx + x, gy + y, w, h), 2)
        _text(screen, f"T{e['idx']}", gx + x + 2, gy + max(0, y - 16),
              13, col)


def _draw_panel(screen, hud: dict) -> None:
    px = GAME_W
    pygame.draw.rect(screen, PANEL_BG, (px, 0, PANEL_W, WIN_H))
    x = px + 14
    y = 10
    who = "JEV DECIDES →" if hud["brain"] == "jev" else "BASELINE →"
    _text(screen, who, x, y, 12, DIM)
    y += 20
    ans = hud["answers"]
    if ans is None:
        if hud["brain"] == "heuristic":
            _text(screen, hud.get("base_reason", "…"), x, y, 15, TXT)
            y += 30
        else:
            _text(screen, "…", x, y, 40, DIM)
            y += 52
    elif hud["brain"] == "jev":
        tgt = (ans.get("target") or {}).get("choice", "?")
        tconf = (ans.get("target") or {}).get("confidence", 0.0)
        fire = (ans.get("fire") or {}).get("choice", "?")
        fconf = (ans.get("fire") or {}).get("confidence", 0.0)
        big = f"T{tgt} {str(fire).upper()}"
        _text(screen, big, x, y, 40)
        _text(screen, f"{max(tconf, fconf):.0%}", x + 250, y + 8, 26, DIM)
        y += 52
        _text(screen, f"target T{tgt}", x, y, 12, DIM)
        _bar(screen, x + 130, y + 1, 150, 12, tconf)
        _text(screen, f"{tconf:.0%}", x + 288, y, 12, DIM)
        y += 18
        _text(screen, f"fire {fire}", x, y, 12, DIM)
        _bar(screen, x + 130, y + 1, 150, 12, fconf,
              RED if fire == "shoot" else BLUE)
        _text(screen, f"{fconf:.0%}", x + 288, y, 12, DIM)
        y += 18
        danger = (ans.get("danger") or {}).get("score", 0.0)
        _text(screen, "danger", x, y, 12, DIM)
        _bar(screen, x + 130, y + 1, 150, 12, danger / 2.0, AMBER)
        _text(screen, f"{danger:.2f}", x + 288, y, 12, DIM)
        y += 24
    else:
        _text(screen, hud.get("base_reason", "…"), x, y, 15, TXT)
        y += 30

    y = _section(screen, x, y, "CONTEXT · ENEMIES")
    if not hud["enemies"]:
        _text(screen, "— none —", x, y, 12, DIM)
        y += 16
    for e in hud["enemies"][:8]:
        mark = "●" if e["visible"] else "○"
        col = (GREEN if e["idx"] == hud["target_id"] and
               hud["target_id"] is not None else TXT)
        s = (f"T{e['idx']} {mark} {e['type'][:12]:<12} "
             f"{e['side']:<9} {e['bearing']:+6.1f} {e['dist']:>4.0f}u")
        _text(screen, s, x, y, 12, col)
        y += 16
    y += 4

    y = _section(screen, x, y, "LATENCY")
    y = _kv(screen, x, y, "Jev, last", f"{hud['ms_last']:.0f}ms",
            "Jev, avg", f"{hud['ms_avg']:.0f}ms")
    y = _kv(screen, x, y, "Decisions", str(hud["decisions"]),
            "API errors", str(hud["errors"]))

    y = _section(screen, x, y, "TOKENS & GAME")
    y = _kv(screen, x, y, "Tokens in", f"{hud['tok_in']:,}",
            "hp", f"{hud['hp']:.0f}")
    y = _kv(screen, x, y, "Tokens out", f"{hud['tok_out']:,}",
            "ammo / kills", f"{hud['ammo']} / {hud['kills']}")
    y = _kv(screen, x, y, "focus", str(hud["focus_id"]),
            "lead_tics", str(hud["lead_tics"]))

    y = _section(screen, x, y, "RECENT DECISIONS")
    for r in reversed(hud["recent"]):
        _text(screen, f"{r['d']:<4}{r['label']:<14}", x, y, 12,
              RED if "SHOOT" in r["label"] or "FIRE" in r["label"]
              else TXT)
        _bar(screen, x + 190, y + 1, 110, 11, r["conf"])
        _text(screen, f"{r['conf']:.0%} {r['ms']:.0f}ms", x + 308, y, 12,
              DIM)
        y += 16


def _section(screen, x: int, y: int, title: str) -> int:
    _text(screen, title, x, y, 12, DIM)
    y += 16
    pygame.draw.line(screen, LINE, (x, y), (WIN_W - 14, y), 1)
    return y + 6


def _kv(screen, x: int, y: int, k1: str, v1: str,
        k2: str, v2: str) -> int:
    _text(screen, k1, x, y, 12, DIM)
    _text(screen, v1, x + 130, y, 12)
    _text(screen, k2, x + 220, y, 12, DIM)
    _text(screen, v2, x + 320, y, 12)
    return y + 16


def _wstep(game, action: list, tics: int, hud: dict,
           screen, game_surf, pace: bool = False) -> bool:
    """2-tic chunks + frame grab + render + event pump. False on quit.

    pace=True mirrors play._step(pace=True): latency-gap holds cost game
    time at the live-play rate (35 tics/s) instead of spinning hundreds
    of tics through an instant emulator while the next answer is in
    flight. Decision actions run unpaced (game tics are game tics).
    """
    import time as _t
    for _done in range(0, tics, 2):
        game.make_action(action, min(2, tics - _done))
        if game.is_episode_finished():
            return not hud["quit"]
        _grab(game, hud)
        _pump(hud)
        if hud["quit"]:
            return False
        render(screen, game_surf, hud)
        if pace:
            _t.sleep((2 / 35) * (min(2, tics - _done) / 2))
    return True


def _wtrack(game, cur: list, done, target_id: int | None,
            fire_ok: bool, hud: dict, screen, game_surf) -> int:
    """Latency-gap tracker that renders while it holds (mirrors play)."""
    if target_id is None:
        return _wextend(game, cur, done, False, hud, screen, game_surf)
    n = len(cur)
    stepped = 0
    while not done() and not game.is_episode_finished() and not hud["quit"]:
        st = game.get_state()
        rel = bearing_to(st, list(st.game_variables), target_id) if st else None
        if rel is None:
            return stepped + _wextend(game, cur, done, False, hud, screen,
                                      game_surf)
        b, _dist, vis = rel
        ammo = float(st.game_variables[1])
        if abs(b) <= C.CENTER_DEGREES:
            if fire_ok and vis and ammo > 0:
                if not _wstep(game, [0, 0, 1], C.FIRE_TICS, hud, screen,
                              game_surf, pace=True):
                    return stepped
                if not _wstep(game, [0] * n, C.RELEASE_TICS, hud, screen,
                              game_surf, pace=True):
                    return stepped
                stepped += C.FIRE_TICS + C.RELEASE_TICS
            else:
                if not _wstep(game, [0] * n, 2, hud, screen, game_surf,
                              pace=True):
                    return stepped
                stepped += 2
            continue
        vec = [1, 0, 0] if b < 0 else [0, 1, 0]
        tics = min(C.TURN_TICS_MAX,
                   max(C.TURN_TICS_MIN, round(abs(b) / C.TURN_DEG_PER_TIC)))
        if not _wstep(game, vec, tics, hud, screen, game_surf, pace=True):
            return stepped
        stepped += tics
    return stepped


def _wextend(game, action: list, done, cycle_attack: bool, hud: dict,
             screen, game_surf) -> int:
    n = len(action)
    press = ([(action, C.FIRE_TICS), ([0] * n, C.RELEASE_TICS)]
             if cycle_attack else [(action, 2)])
    i, stepped = 0, 0
    while not done() and not game.is_episode_finished() and not hud["quit"]:
        a, t = press[i % len(press)]
        if not _wstep(game, a, t, hud, screen, game_surf, pace=True):
            return stepped
        stepped += t
        i += 1
    return stepped


def _note_recent(hud: dict, label: str, conf: float, ms: float) -> None:
    hud["recent"].append({"d": hud["decisions"], "label": label,
                          "conf": conf, "ms": ms})


def run_jev_episode(game, log, hud: dict, screen, game_surf,
                    client, seed: int | None = None) -> dict:
    if seed is not None:
        game.set_seed(seed)
    hud["seed"] = seed
    hud["t0"] = time.time()
    game.new_episode()
    reset_episode()
    decisions, latencies, shots = 0, [], 0
    atk_idx = C.ATTACK_IDX
    n_btn = len(game.get_available_buttons())
    cur = [0] * n_btn
    cycle = False
    engaged, focus, focus_prev_kills = {}, None, None
    recent: deque = deque(maxlen=5)
    prev = None
    gap_ema, fire_ok = 0.0, False

    def fresh():
        nonlocal focus, focus_prev_kills
        state = game.get_state()
        if state is None:
            return None
        lead = _lead(gap_ema, focus, cur)
        snap = encode(state, list(state.game_variables), last=prev,
                      recent=list(recent), lead=lead)
        focus = _update_focus(engaged, focus, snap["enemies"],
                              snap["player"]["kills"], focus_prev_kills)
        focus_prev_kills = snap["player"]["kills"]
        snap["focus"] = _public_focus(focus)
        return snap

    with ThreadPoolExecutor(max_workers=1) as ex:
        snapshot = fresh()
        if snapshot is None:
            return {"decisions": 0, "shots": 0, "avg_ms": 0,
                    "reward": game.get_total_reward()}
        pending = snapshot
        fut = ex.submit(decide, client, snapshot, "defend")
        while not game.is_episode_finished() and not hud["quit"]:
            if not fut.done():
                gap = _wtrack(game, cur, fut.done, (focus or {}).get("id"),
                              fire_ok, hud, screen, game_surf)
                gap_ema = gap if gap_ema == 0 else gap_ema + 0.2 * (gap - gap_ema)
                continue
            try:
                answers, usage, ms = fut.result()
            except Exception as e:
                print(f"  [warn] jev error: {e}, holding", flush=True)
                hud["errors"] += 1
                if not _wstep(game, [0] * n_btn, C.TURN_TICS, hud, screen,
                              game_surf):
                    break
                snapshot = fresh()
                if snapshot is None:
                    break
                pending = snapshot
                fut = ex.submit(decide, client, snapshot, "defend")
                continue
            snapshot = pending
            focus_id = (snapshot.get("focus") or {}).get("id")
            action, tics, reason = to_action(answers, snapshot, None,
                                            "defend")
            tgt = picked_target(answers, snapshot)
            if tgt is not None:
                focus = {"id": tgt["id"], "type": tgt["type"],
                         "bearing": tgt["bearing"], "dist": tgt["dist"],
                         "engaged": engaged.get(tgt["id"], {}).get("engaged", 0),
                         "misses": 0}
            fire_ok = answers.get("fire", {}).get("choice") == "shoot"
            hud["target_id"] = (tgt["idx"] if tgt is not None else None)
            hud["fire_ok"] = fire_ok
            if action[atk_idx]:
                shots += 1
                hud["fired_at"] = decisions + 1
                if not _wstep(game, action, tics, hud, screen, game_surf):
                    break
                if not _wstep(game, [0] * len(action), C.RELEASE_TICS, hud,
                              screen, game_surf):
                    break
            else:
                if not _wstep(game, action, tics, hud, screen, game_surf):
                    break
            cur, cycle = action, bool(action[atk_idx])
            decisions += 1
            latencies.append(ms)
            hud.update({
                "answers": answers, "snapshot": snapshot,
                "enemies": snapshot["enemies"],
                "decisions": decisions, "shots": shots,
                "ms_last": ms,
                "ms_avg": sum(latencies) / len(latencies),
                "tok_in": hud["tok_in"] + usage.get("input_tokens", 0),
                "tok_out": hud["tok_out"] + usage.get("output_tokens", 0),
                "hp": snapshot["player"]["health"],
                "ammo": snapshot["player"]["ammo"],
                "kills": snapshot["player"]["kills"],
                "focus_id": focus_id,
                "lead_tics": snapshot.get("lead_tics", 0),
            })
            _grab(game, hud)
            f = (answers.get("fire") or {}).get("choice", "?")
            t = (answers.get("target") or {}).get("choice", "?")
            tc = (answers.get("target") or {}).get("confidence", 0.0)
            _note_recent(hud, f"T{t} {str(f).upper()}", tc, ms)
            prev = {"action": reason,
                    "hp_change": round(snapshot["player"]["health"] - prev_hp, 1)
                    if decisions > 1 else 0,
                    "ammo_used": round(prev_ammo - snapshot["player"]["ammo"], 1)
                    if decisions > 1 else 0,
                    "kills_change": (snapshot["player"]["kills"] - prev_kills)
                    if decisions > 1 else 0}
            recent.append(prev)
            prev_hp, prev_ammo, prev_kills = (snapshot["player"]["health"],
                                             snapshot["player"]["ammo"],
                                             snapshot["player"]["kills"])
            log({
                "target": t,
                "target_conf": round(tc, 3),
                "lead_tics": snapshot.get("lead_tics", 0),
                **_fire_log_fields(answers),
                "danger": round(answers["danger"]["score"], 2),
                "action": action, "reason": reason, "ms": round(ms),
                "in_tok": usage.get("input_tokens", 0),
                "out_tok": usage.get("output_tokens", 0),
                "hp": snapshot["player"]["health"],
                "ammo": snapshot["player"]["ammo"],
                "kills": snapshot["player"]["kills"],
                "focus": focus_id,
            })
            print(f"  d{decisions}: target={t} "
                  f"fire={f} danger={answers['danger']['score']:.2f} "
                  f"hp={snapshot['player']['health']:.0f} "
                  f"kills={snapshot['player']['kills']} [{ms:.0f}ms]",
                  flush=True)
            snapshot = fresh()
            if snapshot is None:
                break
            pending = snapshot
            fut = ex.submit(decide, client, snapshot, "defend")
    return {"decisions": decisions, "shots": shots,
            "avg_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
            "reward": game.get_total_reward()}


def run_heuristic_episode(game, log, hud: dict, screen, game_surf,
                          seed: int | None = None) -> dict:
    if seed is not None:
        game.set_seed(seed)
    hud["seed"] = seed
    hud["t0"] = time.time()
    game.new_episode()
    hev.reset_episode()
    decisions, shots = 0, 0
    atk_idx = C.ATTACK_IDX
    hud["answers"] = None
    while not game.is_episode_finished() and not hud["quit"]:
        state = game.get_state()
        if state is None:
            break
        snapshot = encode(state, list(state.game_variables))
        action, tics, reason = hev.heuristic_action(snapshot)
        hud["target_id"] = None
        if action[atk_idx]:
            shots += 1
            hud["fired_at"] = decisions + 1
            if not _wstep(game, action, tics, hud, screen, game_surf):
                break
            if not _wstep(game, [0] * len(action), C.RELEASE_TICS, hud,
                          screen, game_surf):
                break
        else:
            if not _wstep(game, action, tics, hud, screen, game_surf):
                break
        decisions += 1
        fired = bool(action[atk_idx])
        hud.update({
            "snapshot": snapshot, "enemies": snapshot["enemies"],
            "decisions": decisions, "shots": shots,
            "base_reason": reason,
            "hp": snapshot["player"]["health"],
            "ammo": snapshot["player"]["ammo"],
            "kills": snapshot["player"]["kills"],
        })
        _grab(game, hud)
        _note_recent(hud, ("FIRE" if fired else reason[:14]), 1.0, 0.0)
        log({
            "aim": ("center" if fired else
                    "left" if action[0] else "right" if action[1] else "center"),
            "aim_conf": 1.0, "fire": 1.0 if fired else 0.0,
            "danger": hev.danger_of(snapshot),
            "action": action, "reason": reason, "ms": 0,
            "in_tok": 0, "out_tok": 0,
            "hp": snapshot["player"]["health"],
            "ammo": snapshot["player"]["ammo"],
            "kills": snapshot["player"]["kills"],
            "focus": None,
        })
    return {"decisions": decisions, "shots": shots, "avg_ms": 0,
            "reward": game.get_total_reward()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", choices=["jev", "heuristic"], default="jev")
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=2100)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--suite", action="store_true",
                    help=f"run the fixed seed suite {SEED_SUITE}")
    args = ap.parse_args()

    import sys
    if args.brain == "jev" and not C.API_KEY:
        sys.exit("no API key: set TYPESAFE_API_KEY in .env (see .env.example)")

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption(
        "jev-doom watch — ESC quits")
    game_surf = pygame.Surface((GAME_W, GAME_H))

    import datetime
    import json
    from pathlib import Path
    import httpx
    import vizdoom as vzd

    run_dir = Path("runs")
    run_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    game = make_game("defend", visible=False, timeout_tics=args.timeout)
    seeds = (list(SEED_SUITE) if args.suite
             else [args.seed + ep for ep in range(args.episodes)]
             if args.seed is not None else [None] * args.episodes)
    try:
        with httpx.Client(timeout=25) as client:
            for ep, seed in enumerate(seeds):
                hud = new_hud(args.brain)
                hud["ep"] = ep
                suffix = f"_seed{seed}" if seed is not None else ""
                btag = "_heuristic" if args.brain == "heuristic" else ""
                path = run_dir / f"{ts}_defend_ep{ep}{suffix}{btag}.jsonl"
                print(f"episode {ep} (seed={seed}, brain={args.brain}) "
                      f"-> {path}", flush=True)
                with open(path, "w") as f:
                    def log(obj, f=f):
                        f.write(json.dumps(obj) + "\n")
                    if args.brain == "heuristic":
                        s = run_heuristic_episode(game, log, hud, screen,
                                                  game_surf, seed=seed)
                    else:
                        s = run_jev_episode(game, log, hud, screen,
                                            game_surf, client, seed=seed)
                s.update({
                    "hp": float(game.get_game_variable(vzd.GameVariable.HEALTH)),
                    "ammo": float(game.get_game_variable(vzd.GameVariable.AMMO2)),
                    "kills": int(game.get_game_variable(vzd.GameVariable.KILLCOUNT)),
                    "seed": seed,
                })
                print(f"episode {ep} done: {s}", flush=True)
                with open(str(path).replace(".jsonl", ".summary.json"),
                          "w") as f:
                    json.dump({**s, "scenario": "defend",
                               "brain": args.brain, "path": str(path)}, f,
                              indent=1)
                if hud["quit"]:
                    print("quit by user", flush=True)
                    break
    finally:
        game.close()
        pygame.quit()


if __name__ == "__main__":
    main()
