#!/usr/bin/env python3
"""snake-view: big replay of the finished snake games logged by the harness. The live footer snake writes every won/lost game to ``SNAKE_LOG`` (``.snake-games.jsonl``) as one JSON line each.  This tool reads that log and replays the games on a large, colourful board in the terminal so the tiny footer games can be studied at full size. usage (default = live console-sized simulation): uv run snake-view                 # simulate a fresh console-sized game uv run snake-view games.jsonl     # replay that log of finished games uv run snake-view --simulate      # force live simulation uv run snake-view --list [log]    # summarise a log instead of animating uv run snake-view [--game N] [--scale N] [--fps N]"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass

from snake import (
    GameConfig,
    SnakeGame,
    default_game_log,
)

RESET = "\x1b[0m"
DIM = "\x1b[2m"
# Per-pixel colour: head a bright green, body a deeper green, food red.
HEAD = "\x1b[38;2;130;255;130m"
BODY = "\x1b[38;2;0;190;85m"
FOOD = "\x1b[38;2;255;70;70m"
SPACE = "\x1b[38;2;45;50;55m"
OUTCOME = {
    "won": "\x1b[38;2;120;255;120m",
    "lost": "\x1b[38;2;255;120;120m",
}


@dataclass
class Game:
    """One finished game read from the harness log."""

    outcome: str
    cols: int
    rows: int
    width: int
    height: int
    initial_length: int
    foods: int
    max_length: int
    tick_count: int
    steps: int
    avg_depth: float
    moves: list  # [(dx, dy), ...]
    food_trace: list  # food cell (or None) at each move

    @staticmethod
    def from_dict(raw: dict) -> "Game":
        return Game(
            outcome=str(raw.get("outcome", "lost")),
            cols=int(raw.get("cols", 0)),
            rows=int(raw.get("rows", 0)),
            width=int(raw.get("width", 0)),
            height=int(raw.get("height", 0)),
            initial_length=int(raw.get("initial_length", 3)),
            foods=int(raw.get("foods", 0)),
            max_length=int(raw.get("max_length", 0)),
            tick_count=int(raw.get("tick_count", 0)),
            steps=int(raw.get("steps", 0)),
            avg_depth=float(raw.get("avg_depth", 0.0)),
            moves=list(raw.get("moves") or []),
            food_trace=list(raw.get("food_trace") or []),
        )


def load_games(path: str) -> list[Game]:
    """Parse the JSON-lines log; return games in log order."""
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"no snake game log at {path!r} (run the harness to make one)"
        )
    games: list[Game] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            games.append(Game.from_dict(json.loads(line)))
    if not games:
        raise ValueError(f"{path!r} contains no finished games")
    return games


def replay_frames(game: Game):
    """Yield (snake, food) snapshots reconstructed from the recorded moves. The snake starts in the middle (mirroring SnakeGame.reset) and follows the recorded heading deltas; a move that lands on that step's food grows. Logs without a food trace (older records) replay as a constant-length path, which is the faithful fallback when no positions were stored."""
    width, height = game.width, game.height
    cx, cy = width // 2, height // 2
    snake = [(cx - i, cy) for i in range(game.initial_length) if 0 <= cx - i < width]
    if not snake:
        snake = [(0, cy)]
    if not game.moves:  # died before moving / empty game
        yield list(snake), None
        return
    for i, (dx, dy) in enumerate(game.moves):
        food = game.food_trace[i] if i < len(game.food_trace) else None
        nh = (snake[0][0] + dx, snake[0][1] + dy)
        # Eat (grow) when landing on this step's food, else the tail vacates.
        snake = [nh] + (snake if food is not None and nh == food else snake[:-1])
        yield list(snake), food


def cell_style(x: int, y: int, snake: list, food) -> str:
    """ANSI-coloured rune for one playfield pixel at (x, y)."""
    if food is not None and (x, y) == tuple(food):
        return FOOD + "●"
    if (x, y) == tuple(snake[0]):
        return HEAD + "█"
    if (x, y) in snake:
        return BODY + "█"
    return SPACE + "░"


def render(game: Game, snake: list, food, scale: int = 2) -> list[str]:
    """Render the board as ``scale``-sized blocks with a border + title."""
    w, h = game.width, game.height
    cw, ch = scale * 2, scale  # terminal columns/rows per playfield pixel
    body = [tuple(c) for c in snake]
    rows: list[str] = []
    for y in range(h):
        row = "".join(cell_style(x, y, body, food) for x in range(w) for _ in range(cw))
        rows.extend(row for _ in range(ch))
    top = "┏" + "━" * (w * cw) + "┓"
    bottom = "┗" + "━" * (w * cw) + "┛"
    return [top, *rows, bottom]


def title(game: Game, index: int, total: int) -> str:
    colour = OUTCOME.get(game.outcome, OUTCOME["lost"])
    return (
        f"{colour}Game {index + 1}/{total} ({game.outcome}){RESET} "
        f"· {game.cols}x{game.rows} cell board · {game.foods} food · "
        f"len {game.max_length} · {game.tick_count} ticks · "
        f"{game.steps} moves · depth {game.avg_depth:.1f}"
    )


def _draw(lines: list[str], nlines: int | None) -> int:
    """Print one in-place frame at ``lines``, returning the frame height."""
    if nlines is None:
        nlines = len(lines)
        print("\n".join(lines))
    else:
        sys.stdout.write(f"\x1b[{nlines}A")
        sys.stdout.write("\n".join(lines))
    sys.stdout.flush()
    return nlines


def run_replay(games: list[Game], scale: int, fps: float) -> None:
    """Animate every game in place, overwriting the previous board each frame."""
    delay = 1.0 / fps
    for gid, game in enumerate(games):
        header = title(game, gid, len(games)) + RESET
        nlines = None
        for snake, food in replay_frames(game):
            nlines = _draw([header, *render(game, snake, food, scale)], nlines)
            time.sleep(delay)
        # Hold the ending (win/loss) board for a beat before moving on.
        time.sleep(0.6 if game.outcome == "won" else 0.25)
        if gid < len(games) - 1:
            print()
    print(f"\n{DIM}\u2014 end of replay \u2014{RESET}")


def summarize(games: list[Game]) -> None:
    print(
        f"{'#':>3}  {'outcome':<5} {'board':<7} {'food':>4} {'max':>4} "
        f"{'moves':>5} {'ticks':>6} {'depth':>5}"
    )
    print("—" * 52)
    for i, g in enumerate(games):
        print(
            f"{i + 1:>3}  {g.outcome:<5} {g.cols * 2}x{g.rows * 4:<3} "
            f"{g.foods:>4} {g.max_length:>4} {g.steps:>5} {g.tick_count:>6} "
            f"{g.avg_depth:>5.1f}"
        )
    wins = sum(1 for g in games if g.outcome == "won")
    print(f"— {len(games)} games, {wins} won, {len(games) - wins} lost\n")


def simulate_console(fps: float, log_path: str | None) -> None:
    """Play a fresh, full-console-sized snake live (the default mode). Builds a SnakeGame sized to the current terminal (one cell per terminal char, braille sub-pixels inside), steps it in place like the harness footer but covering the whole screen, and keeps playing through each finished game -- logging wins/losses to ``log_path`` for later replay. Ctrl-C stops it."""
    try:
        cols, rows = os.get_terminal_size()
    except OSError:
        cols, rows = 80, 24
    cols = max(10, cols - 2)
    rows = max(8, rows - 2)
    game = SnakeGame(
        cols, rows, config=GameConfig(seed=None, search_nodes=None, log_path=log_path)
    )
    delay = 1.0 / max(1.0, fps)
    try:
        sys.stdout.write("\x1b[2J\x1b[H")  # clear once
        sys.stdout.flush()
        nlines = None
        while True:
            game.advance()
            colour = (
                OUTCOME["won"] if game.won else OUTCOME["lost"] if game.dead else ""
            )
            header = f"{colour}{game.footer_stats()}{RESET}  {DIM}Ctrl-C to stop{RESET}"
            nlines = _draw([header, *game.render()], nlines)
            time.sleep(delay)
    except KeyboardInterrupt:
        print(f"\n{DIM}— snake stopped —{RESET}")
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="snake-view",
        description="Replay the harness's logged snake games on a big board.",
    )
    parser.add_argument(
        "log", nargs="?", help="JSONL log to replay (default: live simulation)"
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="force live console-sized simulation (the default)",
    )
    parser.add_argument(
        "--replay", action="store_true", help="force replay of a finished-games log"
    )
    parser.add_argument(
        "--list", action="store_true", help="just summarise a log of games"
    )
    parser.add_argument("--game", type=int, help="replay only game #N (1-based)")
    parser.add_argument(
        "--scale", type=int, default=2, help="pixel block size (bigger = larger board)"
    )
    parser.add_argument(
        "--fps", type=float, default=20.0, help="frame rate (simulation and replay)"
    )
    parser.add_argument(
        "--one",
        action="store_true",
        help="play a single game non-interactively (first)",
    )
    args = parser.parse_args(argv)
    fps = max(1.0, args.fps)
    scale = max(1, args.scale)

    # Replay path: an explicit log path, --replay, or --list all need a log.
    replay = args.replay or args.list or args.game is not None or args.log is not None
    if args.simulate or not replay:
        # Default mode (and --simulate): a live console-sized snake game.
        simulate_console(fps, default_game_log())
        return 0

    path = args.log or default_game_log()
    try:
        games = load_games(path)
    except (FileNotFoundError, ValueError) as error:
        print(f"snake-view: {error}", file=sys.stderr)
        return 1

    if args.list:
        summarize(games)
        return 0

    if args.game is not None:
        if not 1 <= args.game <= len(games):
            print(
                f"snake-view: game #{args.game} out of range (1..{len(games)})",
                file=sys.stderr,
            )
            return 1
        games = [games[args.game - 1]]
    elif args.one:
        games = games[:1]
    elif len(games) > 1 and sys.stdin.isatty():
        summarize(games)
        choice = input(f"replay game # (blank = all, q = quit) [1..{len(games)}]: ")
        choice = choice.strip().lower()
        if choice in ("q", "quit"):
            return 0
        if choice:
            try:
                n = int(choice)
            except ValueError:
                n = 0
            if 1 <= n <= len(games):
                games = [games[n - 1]]
    run_replay(games, scale, fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
