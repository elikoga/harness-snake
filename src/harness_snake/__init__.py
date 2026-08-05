"""Braille snake game rendered as a 2-line-high live spinner. The playfield is a grid of braille sub-pixels: every terminal line is one row of braille glyphs and each glyph packs a 2-wide x 4-tall dot block.  A 2-line spinner therefore gives an 8-pixel-tall field whose width scales to the terminal.  The snake grows when it reaches food and the food blinks; the snake plays itself with an iteratively-deepening expectimax game-tree search over directions: eating rewards it, the game respawns food at random chance nodes, death is penalised, a greedy + Manhattan heuristic biases the leaf value, and branch-and-bound pruning keeps the search cheap.  A retained tree carries each state's best move across ticks, re-planting last tick's chosen subtree so steady-state crawl searches deeper for the same per-decision budget (wall-clock time in production; a fixed node count in tests keeps play seed-reproducible)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from random import Random
from time import monotonic
from typing import Iterator

# Optional machine-code search accelerator: the compiled snakecore C library
# (build/libsnakecore.so) loaded through ctypes -- the same C engine the JS
# WASM/native backends use. When the library isn't present/loadable we fall
# back to the pure-Python expectimax; everything below works either way.
try:
    from .native import native_available, load_native

    _native = load_native() if native_available() else None
except Exception:  # pragma: no cover - library not built
    _native = None

# A search state's identity: the snake body plus the food cell (or None if
# just eaten, before respawn).  Used as the key of the retained search tree.
StateKey = tuple[tuple[tuple[int, int], ...], tuple[int, int] | None]

# Compact braille-snake footer: 6 chars wide, 2 rows tall, so the spinner is a
# small game rather than a full-width bar.
SNAKE_FOOTER_COLS = 6
SNAKE_FOOTER_ROWS = 2
SNAKE_TICK = 0.025  # seconds between animation frames (~20 FPS)
SNAKE_STEPS = 1  # snake advances 1 cell per frame for a steady crawl
# How long a dead snake keeps flashing before a fresh game starts.
SNAKE_DEATH_FLASH = 1.0  # seconds of death-flash animation
SNAKE_WIN_FLASH = 10.0  # seconds the winning board keeps flashing
# (>= 10s: the celebration for filling the whole board must linger)

# Game-tree search rewards (signed; all are ADDED in the search; costs are negative).
FOOD_REWARD = 2_500.0  # reward for eating a food pellet
# Death must outweigh 1000 steps + 1000 turns (305k): survival comes first,
# and only filling the board (WIN_REWARD, far above) justifies a dying move.
DEATH_REWARD = -1_000_000.0  # winning the field only beats this (see WIN_REWARD)
# Filling the board is a real win: above any food-only death, below a full one.
WIN_REWARD = 1_000_000.0
MANHATTAN_WEIGHT = 25.0  # greedy bias: Manhattan distance to the food
STEP_REWARD = 5.0  # small reward per step advanced: gently encourages staying alive
TURN_REWARD = -300.0  # penalty for turning: favors straight lines, never dying
# Production: wall-clock per-decision deadline; tests: fixed node count.
SEARCH_TIME = 0.02  # seconds: baseline wall-clock per-decision budget at game start
# As the board fills every move is do-or-die, so the budget ramps toward MAX,
# while shrinking free space keeps the deeper lookahead cheap inside the tick.
SEARCH_TIME_MAX = 0.10  # seconds: per-decision budget once the board is nearly full
SEARCH_RAMP_START = 0.70  # fraction of the board filled where the budget starts growing
FOOD_SAMPLES = 3  # random food respawns averaged at each game chance node

# Default relative path (in the workspace) where finished games are logged as
# JSON lines, one per game, for offline analysis of won and lost games.
SNAKE_LOG = ".snake-games.jsonl"

# Braille base codepoint; each glyph is a 2x4 dot grid.
BRAILLE_BASE = 0x2800
# Dot -> braille bit.  A (py, px) sub-pixel inside a glyph maps to a bit:
# bit0..bit3 are the top row (left,right pairs), bit4..bit7 the bottom row.
DOT_BITS: dict[tuple[int, int], int] = {
    (0, 0): 0x01,
    (0, 1): 0x08,
    (1, 0): 0x02,
    (1, 1): 0x10,
    (2, 0): 0x04,
    (2, 1): 0x20,
    (3, 0): 0x40,
    (3, 1): 0x80,
}
# Sub-pixel width/height of a single braille glyph.
CELL_W, CELL_H = 2, 4


@dataclass
class GameConfig:
    """Tunable gameplay knobs, kept on one object for tests. ``log_path`` (when set) makes every finished game -- a win by filling the board or a loss by dying -- append one JSON line to that file for offline analysis.  It is off by default so pure tests and other embedders write nothing."""

    initial_length: int = 3
    food_flash_period: int = 2  # ticks between food blink states
    seed: int | None = None  # deterministic food placement for tests
    search_nodes: int | None = None  # deterministic node budget; None->clock
    log_path: str | None = None  # JSONL sink for finished games (analysis)


@dataclass
class GameRecord:
    """One finished snake game, captured for offline analysis. Emitted whenever a game ends: a win by filling the whole board (``outcome == "won"``) or a loss by dying (``outcome == "lost"``). Kept as plain data so it serialises to one JSON line per game for later analysis of which games were won/lost, how long they lived, and the search behaviour behind each result."""

    outcome: str  # "won" if the board was filled, else "lost"
    cols: int  # playfield width in terminal cells
    rows: int  # playfield height in terminal rows
    width: int  # playfield width in sub-pixels
    height: int  # playfield height in sub-pixels
    initial_length: int
    seed: int | None
    search_nodes: int | None
    foods: int  # food pellets eaten
    steps: int  # successful (non-fatal) moves made
    final_length: int  # snake length the moment the game ended
    max_length: int  # longest the snake ever reached during the game
    tick_count: int  # frames played through the fatal tick
    decisions: int  # search decisions made
    avg_depth: float  # average search ply reached per decision
    max_depth: int  # deepest ply the search reached
    avg_search_ms: float  # average wall-clock per-decision search cost
    moves: tuple[tuple[int, int], ...]  # chosen heading deltas, in order
    finished_at: float  # monotonic() when the game was finalised
    food_trace: tuple[tuple[int, int] | None, ...] = ()  # food per move (replay)


def default_game_log(root: str | None = None) -> str:
    """Absolute path of the default finished-games log in ``root``. ``root`` defaults to the current working directory (the harness workspace when the footer snake runs).  The log is a single JSONL file that accumulates every finished game so won/lost records survive across runs."""
    return os.path.join(os.path.abspath(root or os.getcwd()), SNAKE_LOG)


def append_game_log(record: GameRecord, path: str) -> None:
    """Append ``record`` as one JSON line to ``path`` (creating it). Uses an append-open so concurrent appends never overwrite each other; the line is flushed so a crash mid-game still leaves earlier finished games on disk.  Callers that want zero persistence just leave ``log_path`` unset."""
    line = json.dumps(asdict(record), separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()


class SnakeGame:
    """One self-playing snake lifecycle, stepping toward longest life. Renders itself as a footer widget (``render()`` returns the snake as terminal rows of braille glyphs) and ``advance()`` drives the full animation lifecycle (step, dead-hold flash, reset)."""

    def __init__(
        self,
        cols: int,
        rows: int = 2,
        config: GameConfig | None = None,
    ) -> None:
        # cols/rows are terminal cells; the playfield is sub-pixels of them.
        self.config = config or GameConfig()
        self.cols = max(1, cols)
        self.lines = max(1, rows)
        self.width = self.cols * CELL_W  # playfield width in pixels
        self.height = self.lines * CELL_H  # playfield height in pixels
        self._rng = Random(self.config.seed)
        # Retained search tree: state -> best move, reused across ticks so
        # steady-state crawl searches deeper for the same per-decision budget.
        # All per-game fields are (re)initialised by reset() below.
        self._tt: dict[StateKey, tuple[int, int]] = {}
        self._tt_limit = 4096  # bound retained-tree memory (cleared deterministically)
        self._nodes_left = 0  # per-decision node budget (tests); unused for time path
        self._deadline = 0.0  # per-decision wall-clock deadline (production path)
        self.reset()

    # ---- lifecycle ----------------------------------------------------
    def reset(self) -> None:
        """Start a fresh snake in the middle, growing leftward."""
        cx = self.width // 2
        cy = self.height // 2
        start = self.config.initial_length
        self.snake = [(cx - i, cy) for i in range(start) if 0 <= cx - i < self.width]
        if not self.snake:
            self.snake = [(0, cy)]
        self.growing = False
        self.dead_flag = False
        self.won = False
        self.dead_ticks = 0
        self.tick_count = 0
        self._death_started = None
        self.decisions = 0
        self.last_search_ms = 0.0
        self.last_depth = 0
        self.foods = 0
        self.max_length = len(self.snake)
        self._moves = []
        self._food_trace = []
        self._depth_sum = 0.0
        self._search_ms_sum = 0.0
        self._max_depth = 0
        self.food = None
        self._tt.clear()  # a fresh board invalidates old retained subtrees
        self._spawn_food()

    @property
    def dead(self) -> bool:
        return self.dead_flag

    @property
    def head(self) -> tuple[int, int]:
        return self.snake[0]

    # ---- state helpers ------------------------------------------------
    def _in_bounds(self, cell: tuple[int, int]) -> bool:
        x, y = cell
        return 0 <= x < self.width and 0 <= y < self.height

    def _spawn_food(self) -> None:
        """Place food on a random unoccupied cell, if any remain."""
        free = self._free_cells(tuple(self.snake))
        self.food = self._rng.choice(free) if free else None

    def _food_visible(self) -> bool:
        """Food flashes: alternate on/off on each flash period."""
        if self.food is None:
            return False
        return (self.tick_count // self.config.food_flash_period) % 2 == 0

    # ---- movement ------------------------------------------------------
    def _neighbors(self, cell: tuple[int, int]) -> Iterator[tuple[int, int]]:
        x, y = cell
        for nx, ny in ((x, y - 1), (x, y + 1), (x - 1, y), (x + 1, y)):
            if self._in_bounds((nx, ny)):
                yield nx, ny

    def _safe_moves(self, grow: bool) -> list[tuple[int, int]]:
        """Neighbors the head may move to without instant collision. The tail square is free this tick unless we are growing."""
        head = self.head
        tail = self.snake[-1]
        body = set(self.snake)
        if not grow:
            body.discard(tail)  # tail vacates its cell this tick
        return [nxt for nxt in self._neighbors(head) if nxt not in body]

    # ---- game-tree search (expectimax over directions) --------------------
    # The snake is modelled as a turn-based game: on each turn it picks a
    # direction; eating food is a positive reward and the game then respawns
    # food on a random free cell (a chance node).  Death is a huge negative
    # reward.  At the search horizon a Manhattan-distance-to-food heuristic
    # biases the leaf value (boxing-in is caught by the search itself as a
    # real death, so no flood-fill reachability is needed), and
    # branch-and-bound pruning skips subtrees that cannot beat the best move
    # found so far.  All simulated states are immutable tuples so the tree is
    # just an iterative-deepening, branch-and-bound search over made-up futures.
    def _grow(self) -> bool:
        return self.growing or (self.food == self.head)

    def _apply_move(
        self,
        snake: tuple[tuple[int, int], ...],
        food: tuple[int, int] | None,
        move: tuple[int, int],
    ) -> tuple[tuple[tuple[int, int], ...], tuple[int, int] | None] | None:
        """Return the (snake, food) state after ``move``, or None on death. The tail vacates its cell this tick unless the move eats food (then the snake grows).  Colliding with the remaining body is death."""
        grow = move == food
        body = set(snake) - set(snake[-1:] if not grow else ())
        if move in body:
            return None
        new_snake = (move,) + snake
        if not grow:
            new_snake = new_snake[:-1]
        return new_snake, (None if grow else food)

    def _free_cells(self, snake: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
        return [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if (x, y) not in snake
        ]

    def _heuristic(
        self, snake: tuple[tuple[int, int], ...], food: tuple[int, int] | None
    ) -> float:
        """Leaf value: Manhattan distance to food (closer is better). Gives the search directional purpose even below the food-reaching depth, so the snake plans toward food and turns less."""
        if not food:
            return 0.0
        return -(self._manhattan(snake[0], food) * MANHATTAN_WEIGHT)

    def _upper_bound(self, depth: int) -> float:
        """Optimistic value ceiling for branch-and-bound pruning. Even a perfect subtree can never exceed eating food on every remaining ply plus the best possible heuristic leaf (the whole board is reachable, worth at most its cell count)."""
        return (
            FOOD_REWARD * depth
            + STEP_REWARD * depth
            + WIN_REWARD
            + self.width * self.height
        )

    @staticmethod
    def _manhattan(a: tuple[int, int], b: tuple[int, int] | None) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) if b else 0

    def _within_budget(self) -> bool:
        """True while the decision still has budget left. Tests set a fixed node count (config.search_nodes) so play is seed-reproducible; production runs on a wall-clock deadline so it can search as deep as the animation tick allows.  The node budget is a pure node count (no wall clock), hence deterministic."""
        if self.config.search_nodes is not None:
            self._nodes_left -= 1
            return self._nodes_left > 0
        return monotonic() < self._deadline

    def _is_turn(self, snake, move: tuple[int, int]) -> bool:
        """True if ``move`` changes the heading (vs continuing straight)."""
        return len(snake) >= 2 and move != (
            snake[0][0] * 2 - snake[1][0],
            snake[0][1] * 2 - snake[1][1],
        )

    def _value(
        self,
        child: tuple[tuple[tuple[int, int], ...], tuple[int, int] | None],
        turn: float,
        depth: int,
        alpha: float = float("-inf"),
        beta: float = float("inf"),
    ) -> float:
        """Value of one child: eating food yields the food reward then a chance node; otherwise take a step into the deeper search."""
        snake, food = child
        if food is None:
            return (
                FOOD_REWARD + STEP_REWARD + turn + self._chance_value(snake, depth - 1)
            )
        return STEP_REWARD + turn + self._search(snake, food, depth - 1, alpha, beta)

    def _search(
        self,
        snake: tuple[tuple[int, int], ...],
        food: tuple[int, int] | None,
        depth: int,
        alpha: float,
        beta: float,
    ) -> float:
        """Expected value of a snake-to-move state (maximizer node)."""
        if not self._within_budget():
            return self._heuristic(snake, food)  # out of budget: bail early
        moves = []
        for move in self._neighbors(snake[0]):
            child = self._apply_move(snake, food, move)
            if child is not None:
                moves.append((move, child))
        if not moves:
            return DEATH_REWARD
        if depth <= 0:
            return self._heuristic(snake, food)
        # Tree reuse: try the move that was best at this state on a previous
        # tick first, then moves heading toward the food.  Better ordering
        # lets branch-and-bound prune sooner, so deeper lookahead stays cheap.
        key = (snake, food)
        cached = self._tt.get(key)
        moves.sort(key=lambda mc: (mc[0] != cached, self._manhattan(mc[0], food)))
        best = float("-inf")
        best_m: tuple[int, int] | None = None
        ceiling = self._upper_bound(depth)
        for move, child in moves:
            turn = TURN_REWARD if self._is_turn(snake, move) else 0.0
            value = self._value(child, turn, depth - 1, alpha, beta)
            if value > best:
                best = value
                best_m = move
                if best > alpha:
                    alpha = best
                if alpha >= beta or best >= ceiling:
                    break  # branch-and-bound: cannot do better
        if best_m is not None:
            self._remember(key, best_m)
        return best

    def _chance_value(self, snake: tuple[tuple[int, int], ...], depth: int) -> float:
        """Game chance node: average value over random food respawns."""
        if not self._within_budget():
            return self._heuristic(snake, None)  # out of budget: bail early
        free = self._free_cells(snake)
        if not free:
            return WIN_REWARD  # board completely filled: snake won the field
        samples = (
            free if len(free) <= FOOD_SAMPLES else self._rng.sample(free, FOOD_SAMPLES)
        )
        # No pruning inside a chance node: every sample is evaluated fully.
        total = 0.0
        for respawn in samples:
            total += self._search(snake, respawn, depth, float("-inf"), float("inf"))
        return total / len(samples)

    def _remember(self, key: StateKey, move: tuple[int, int]) -> None:
        """Record a state's best move for reuse, bounding retained memory. Clearing on reaching the cap is deterministic: two same-seed games explore the same number of states in the same order, so they clear together and stay identical."""
        if len(self._tt) >= self._tt_limit:
            self._tt.clear()
        self._tt[key] = move

    def _search_budget(self) -> float:
        """Wall-clock per-decision search budget: a U-curve over game progress. The snake thinks hardest when the stakes are clearest -- at the very start, while it plans how to collect the first food with plenty of free room, and again as it nears a full board, where each move decides between filling the field and dying.  Mid-game a win is still far off and free space is abundant, so it keeps the cheap base SEARCH_TIME. The shallower branches at both extremes (few occupied cells early, few free cells late) keep this deeper lookahead cheap inside the tick. Node-budgeted tests keep the fixed deterministic SEARCH_TIME so play stays seed-reproducible."""
        if self.config.search_nodes is not None:
            return SEARCH_TIME
        total = self.width * self.height
        filled = len(self.snake) / total
        # Both the opening and the finale span SEARCH_RAMP_START of the board;
        # between them the budget rests at the cheap base.
        region = 1.0 - SEARCH_RAMP_START
        span = SEARCH_TIME_MAX - SEARCH_TIME
        if filled <= region:
            # Opening: elevated, easing down to the base as the snake grows.
            return SEARCH_TIME_MAX - span * (filled / region)
        if filled >= SEARCH_RAMP_START:
            # Finale: base ramping up toward SEARCH_TIME_MAX as it fills.
            return SEARCH_TIME + span * ((filled - SEARCH_RAMP_START) / region)
        return SEARCH_TIME

    def _game_move_py(self) -> tuple[int, int] | None:
        """Best direction by budgeted iterative-deepening expectimax search. Searches deeper and deeper (1, 2, 3, ... plies) until the budget is spent, so it searches as deep as the per-decision budget allows.  The budget is wall-clock time in production (deepen as far as the tick fits) or, in tests, a fixed node count (seed-reproducible).  A retained tree (self._tt) carries each state's best move across ticks, so steady-state crawl re-plants last tick's subtree and reaches deeper for the same budget.  The best move from the deepest complete level wins."""
        moves = self._safe_moves(self._grow())
        if not moves:
            return None
        self._deadline = monotonic() + self._search_budget()
        self._nodes_left = self.config.search_nodes or 0
        moves.sort(key=lambda m: self._manhattan(m, self.food))
        # Tree reuse: try last tick's best move for this exact state first so
        # branch-and-bound can prune its siblings earlier.
        cached = self._tt.get((tuple(self.snake), self.food))
        if cached in moves:
            moves.insert(0, moves.pop(moves.index(cached)))
        best_move: tuple[int, int] | None = moves[0]
        depth = 1
        while True:
            candidate_move: tuple[int, int] | None = None
            candidate_value = float("-inf")
            for move in moves:
                child = self._apply_move(tuple(self.snake), self.food, move)
                if child is None:
                    continue  # defensive; safe moves never die instantly
                turn = TURN_REWARD if self._is_turn(self.snake, move) else 0.0
                value = self._value(child, turn, depth - 1)
                if value > candidate_value:
                    candidate_value, candidate_move = value, move
            if candidate_move is None or not self._within_budget():
                break  # nothing explored this depth, or budget just ran out
            best_move = candidate_move
            depth += 1  # keep deepening while the budget lasts
        self.last_depth = depth - 1  # deepest complete level explored
        self._remember((tuple(self.snake), self.food), best_move)
        return best_move

    def _game_move(self) -> tuple[int, int] | None:
        """Best direction, using the native accelerator when built."""
        start = monotonic()
        move = self._game_move_native() if _native is not None else self._game_move_py()
        self.last_search_ms = (monotonic() - start) * 1000.0
        self.decisions += 1
        self._depth_sum += self.last_depth
        self._search_ms_sum += self.last_search_ms
        self._max_depth = max(self._max_depth, self.last_depth)
        return move

    def _game_move_native(self) -> tuple[int, int] | None:
        """Delegate the decision to the snake_native C extension."""
        moves = self._safe_moves(self._grow())
        if not moves:
            return None
        seed = self.config.seed
        result = _native.choose_move(
            self.width,
            self.height,
            [(x, y) for x, y in self.snake],
            self.food,
            self._grow(),
            seed if seed is not None else 0,
            self.tick_count,
            self.config.search_nodes or 0,
            self._search_budget(),
            FOOD_SAMPLES,
            MANHATTAN_WEIGHT,
            STEP_REWARD,
            TURN_REWARD,
            FOOD_REWARD,
            DEATH_REWARD,
            WIN_REWARD,
        )
        if result is None:
            return None
        move, depth = result
        self.last_depth = depth
        return move

    def footer_stats(self) -> str:
        """Compact live status for the footer row: game + search health. Shared by both search implementations, so it stays meaningful whether the native accelerator or the pure-Python fallback is running."""
        state = "won" if self.won else "dead" if self.dead_flag else "alive"
        return " \u00b7 ".join(
            [
                state,
                f"len {len(self.snake)}",
                f"tick {self.tick_count}",
                # Deepest ply the search reached, and its actual cost vs the
                # wall-clock budget (ms).
                f"depth {self.last_depth}",
                f"search {self.last_search_ms:.0f}/{self._search_budget() * 1000:.0f}ms",
            ]
        )

    def _choose_move(self) -> tuple[int, int] | None:
        """Pick the move that best preserves life; None if stuck/dead. A budgeted, iterative-deepening expectimax game-tree search over directions: eating food is a positive reward, moving is free, turning and dying are penalised, the game respawns food at random chance nodes, a greedy + Manhattan heuristic biases the leaf value, and branch-and-bound pruning keeps deeper lookahead cheap enough to run inside the animation tick."""
        return self._game_move()

    def step(self) -> None:
        """Advance one tick; mark dead when no surviving move remains."""
        if self.dead_flag:
            return
        self.tick_count += 1
        eat = self.food == self.head
        food_at_move = self.food  # food present while making this move
        move = self._choose_move()
        if move is None:
            self.dead_flag = True
            self.food = None
            return
        old_head = self.snake[0]
        if eat:
            self.snake.insert(0, move)  # grow on food
            self._spawn_food()
            self.foods += 1
        else:
            self.snake.insert(0, move)
            self.snake.pop()
        self._moves.append((move[0] - old_head[0], move[1] - old_head[1]))
        self._food_trace.append(food_at_move)
        self.max_length = max(self.max_length, len(self.snake))
        # A snake that fills the board with no way to keep going has won by
        # filling: mark it dead so the game restarts in a long celebration
        # flash (a genuine win, not a death).
        occupied = len(self.snake)
        if occupied >= self.width * self.height and not self.food:
            self.won = True
            self.dead_flag = True
        elif occupied >= self.width * self.height:
            self._spawn_food()

    def _log_finished(self, now: float) -> None:
        """Persist this just-finished game if a ``log_path`` is configured. Called from ``advance`` right before a finished board resets, so the record captures the whole game's outcome, longevity, and search behaviour.  Writes nothing when no sink is configured (tests)."""
        if not self.config.log_path:
            return
        denominator = self.decisions or 1
        record = GameRecord(
            outcome="won" if self.won else "lost",
            cols=self.cols,
            rows=self.lines,
            width=self.width,
            height=self.height,
            initial_length=self.config.initial_length,
            seed=self.config.seed,
            search_nodes=self.config.search_nodes,
            foods=self.foods,
            steps=len(self._moves),
            final_length=len(self.snake),
            max_length=self.max_length,
            tick_count=self.tick_count,
            decisions=self.decisions,
            avg_depth=self._depth_sum / denominator,
            max_depth=self._max_depth,
            avg_search_ms=self._search_ms_sum / denominator,
            moves=tuple(self._moves),
            finished_at=now,
            food_trace=tuple(self._food_trace),
        )
        append_game_log(record, self.config.log_path)

    def advance(self, now: float | None = None) -> None:
        """Advance one animation frame, handling the dead-hold and reset. A live snake steps normally; once dead it keeps advancing the blink counter (so the body flashes) until the death-flash duration elapses, then resets to a fresh game -- the whole animation lifecycle in one call the animator repeats at the tick rate."""
        now = monotonic() if now is None else now
        if self.dead:
            self.dead_ticks += 1
        else:
            self.step()
            if self.dead:
                self._death_started = now
        # A win celebrates for at least SNAKE_WIN_FLASH; a plain death only
        # flashes for the much shorter SNAKE_DEATH_FLASH.
        flash = SNAKE_WIN_FLASH if self.won else SNAKE_DEATH_FLASH
        if (
            self.dead
            and self._death_started is not None
            and now - self._death_started >= flash
        ):
            self._log_finished(now)
            self.reset()
            self._death_started = None

    # ---- rendering -----------------------------------------------------
    def render(self) -> list[str]:
        """Return ``self.lines`` terminal rows of braille glyphs."""
        # (py, px) -> dot bit, over every sub-pixel the snake/food occupy.
        on: dict[tuple[int, int], bool] = {}
        for x, y in self.snake:
            on[(y, x)] = True
        if self._food_visible() and self.food:
            fx, fy = self.food
            on[(fy, fx)] = True
        if self.dead:
            # Flash: blink the snake body on/off for the duration the
            # animator holds the dead state before it resets.  A win blinks
            # faster (every 2 dead-ticks) for a livelier celebration; a plain
            # death toggles every 4 dead-ticks.
            cadence = 2 if self.won else 4
            if (self.dead_ticks // cadence) % 2 == 1:
                on.clear()
        rows: list[str] = []
        for line in range(self.lines):
            glyphs: list[str] = []
            for col in range(self.cols):
                value = 0
                for py in range(CELL_H):
                    for px in range(CELL_W):
                        key = (line * CELL_H + py, col * CELL_W + px)
                        value |= DOT_BITS[(py, px)] * bool(on.get(key))
                glyphs.append(chr(BRAILLE_BASE + value))
            rows.append("".join(glyphs))
        return rows

    def frame(self) -> str:
        return "\n".join(self.render())

    def __repr__(self) -> str:
        return (
            f"SnakeGame({self.cols}x{self.lines} "
            f"len={len(self.snake)} dead={self.dead_flag} won={self.won})"
        )
