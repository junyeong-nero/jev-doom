"""Human keyboard baseline: play the same scenarios as Jev, compare records.

Renders VizDoom frames into a pygame window, so one window handles
both view and input (VizDoom's own window can't capture keys).

Usage:
    uv run python -m doom.human --scenario defend
    uv run python -m doom.human --scenario simple --episodes 2

Controls: LEFT/RIGHT (or A/D) = turn/strafe, SPACE = fire, ESC = quit.
"""
import argparse
import datetime
import json
from pathlib import Path

import numpy as np
import pygame

from .doom_env import make_game

LOG_EVERY_TICS = 7


def read_action(keys, scenario: str = "defend") -> list:
    if scenario == "corridor":
        # [FWD, BACK, LEFT, RIGHT, TLEFT, TRIGHT, ATTACK, SELECT_WEAPON3]
        return [int(bool(keys[pygame.K_w])), int(bool(keys[pygame.K_s])),
                int(bool(keys[pygame.K_a])), int(bool(keys[pygame.K_d])),
                int(bool(keys[pygame.K_LEFT])), int(bool(keys[pygame.K_RIGHT])),
                int(bool(keys[pygame.K_SPACE])), int(bool(keys[pygame.K_q]))]
    left = keys[pygame.K_LEFT] or keys[pygame.K_a]
    right = keys[pygame.K_RIGHT] or keys[pygame.K_d]
    fire = keys[pygame.K_SPACE]
    return [int(bool(left)), int(bool(right)), int(bool(fire))]


def run_human_episode(game, log, scenario: str = "defend",
                      scale: int = 2) -> dict:
    game.new_episode()
    sw, sh = 320, 240
    screen = pygame.display.set_mode((sw * scale, sh * scale))
    pygame.display.set_caption("jev-doom: you vs Jev's record (ESC quits)")
    clock = pygame.time.Clock()

    start_ammo, shots, tic = None, 0, 0
    prev_ammo = None
    while not game.is_episode_finished():
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return {"quit": True, "tic": tic, "shots": shots}
        keys = pygame.key.get_pressed()
        if keys[pygame.K_ESCAPE]:
            return {"quit": True, "tic": tic, "shots": shots}

        action = read_action(keys, scenario)
        state = game.get_state()
        if state is None:
            break
        if state.screen_buffer is not None:
            frame = state.screen_buffer.transpose(1, 2, 0)
            surf = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
            surf = pygame.transform.scale(surf, (sw * scale, sh * scale))
            screen.blit(surf, (0, 0))
            pygame.display.flip()

        vars = list(state.game_variables)
        ammo = float(vars[1])
        if start_ammo is None:
            start_ammo = ammo
        if prev_ammo is not None and ammo < prev_ammo:
            shots += int(prev_ammo - ammo)
        prev_ammo = ammo

        game.make_action(action, 1)
        tic += 1
        if tic % LOG_EVERY_TICS == 0:
            log({"tic": tic, "action": action, "hp": float(vars[0]),
                 "ammo": ammo, "kills": int(vars[2])})

    import vizdoom as vzd
    return {
        "tic": tic,
        "shots": shots,
        "bullets": int(start_ammo - prev_ammo) if start_ammo is not None else 0,
        "hp": float(game.get_game_variable(vzd.GameVariable.HEALTH)),
        "kills": int(game.get_game_variable(vzd.GameVariable.KILLCOUNT)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="defend",
                    choices=["defend", "basic", "simple", "corridor"])
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=2100)
    args = ap.parse_args()

    pygame.init()
    run_dir = Path("runs")
    run_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    game = make_game(args.scenario, visible=False, timeout_tics=args.timeout)
    try:
        for ep in range(args.episodes):
            path = run_dir / f"{ts}_human_{args.scenario}_ep{ep}.jsonl"
            print(f"episode {ep}: play! (corridor: WASD + arrows + space + Q (shotgun) | "
          f"others: arrows + space, ESC quits)", flush=True)
            with open(path, "w") as f:
                def log(obj, f=f):
                    f.write(json.dumps(obj) + "\n")
                s = run_human_episode(game, log, args.scenario)
            s.update({"scenario": args.scenario, "path": str(path)})
            print(f"episode {ep} done: {s}", flush=True)
            with open(run_dir / f"{ts}_human_{args.scenario}_ep{ep}.summary.json",
                      "w") as f:
                json.dump(s, f, indent=1)
            if s.get("quit"):
                print("quit by user", flush=True)
                break
    finally:
        game.close()
        pygame.quit()


if __name__ == "__main__":
    main()
