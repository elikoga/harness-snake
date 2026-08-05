"""ctypes binding to the compiled snakecore C library (build/libsnakecore.so).

Exposes the canonical C engine to Python with no CPython-extension build: we
dlopen the exact same shared object the JS backends use (and the identical
WASM build is what runs in browsers). SnakeGameConfig/SnakeGameResult are
defined with ctypes.Structure so field offsets match csrc/core.h exactly
(5 ints + 4B pad + 9 doubles = 96 bytes; result 6 ints = 24 bytes).

The pure-Python SnakeGame falls back to this when the shared library exists
(search_nodes set -> deterministic node-budget search, byte-identical to the
JS WASM/native backends on the same config).
"""

from __future__ import annotations

import ctypes
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_SO = Path(__file__).resolve().parent.parent.parent / "build" / "libsnakecore.so"


class SnakeGameConfig(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("initial_length", ctypes.c_int),
        ("food_samples", ctypes.c_int),
        ("search_nodes", ctypes.c_int),
        ("_pad", ctypes.c_uint32),  # 5 ints (20B) -> doubles must start at 24
        ("time_budget", ctypes.c_double),
        ("time_budget_max", ctypes.c_double),
        ("ramp_start", ctypes.c_double),
        ("manhattan_weight", ctypes.c_double),
        ("step_reward", ctypes.c_double),
        ("turn_reward", ctypes.c_double),
        ("food_reward", ctypes.c_double),
        ("death_reward", ctypes.c_double),
        ("win_reward", ctypes.c_double),
    ]


class SnakeGameResult(ctypes.Structure):
    _fields_ = [
        ("ticks", ctypes.c_int),
        ("foods", ctypes.c_int),
        ("length", ctypes.c_int),
        ("maxlen", ctypes.c_int),
        ("dead", ctypes.c_int),
        ("filled", ctypes.c_int),
    ]


def build_config(
    *,
    width: int,
    height: int,
    initial_length: int,
    food_samples: int,
    search_nodes: int,
    time_budget: float,
    time_budget_max: float,
    ramp_start: float,
    manhattan_weight: float,
    step_reward: float,
    turn_reward: float,
    food_reward: float,
    death_reward: float,
    win_reward: float,
) -> SnakeGameConfig:
    return SnakeGameConfig(
        width, height, initial_length, food_samples, search_nodes, 0,
        time_budget, time_budget_max, ramp_start, manhattan_weight,
        step_reward, turn_reward, food_reward, death_reward, win_reward,
    )


class SnakeNative:
    """Thin, memoizing ctypes wrapper around one loaded shared object."""

    def __init__(self, so_path: Path = DEFAULT_SO) -> None:
        self.path = Path(so_path)
        if not self.path.is_file():
            raise FileNotFoundError(f"snakecore shared library not found: {self.path}")
        self._lib = ctypes.CDLL(str(self.path))
        self._bind()

    def _bind(self) -> None:
        lib = self._lib
        lib.snake_version.restype = ctypes.c_int
        lib.snake_version.argtypes = []

        lib.snake_choose_move.restype = ctypes.c_int
        lib.snake_choose_move.argtypes = [
            ctypes.POINTER(SnakeGameConfig),
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_int,  # snake_len
            ctypes.c_int,  # food
            ctypes.c_int,  # grow
            ctypes.c_uint64,  # seed
            ctypes.c_int,  # decision_id
            ctypes.c_int,  # node_budget
            ctypes.c_double,  # time_budget
            ctypes.POINTER(ctypes.c_int),  # out_x
            ctypes.POINTER(ctypes.c_int),  # out_y
            ctypes.POINTER(ctypes.c_int),  # out_depth
        ]

        lib.snake_play_game.restype = ctypes.c_int
        lib.snake_play_game.argtypes = [
            ctypes.POINTER(SnakeGameConfig),
            ctypes.c_uint64,  # game_seed
            ctypes.c_int,  # max_ticks
            ctypes.POINTER(SnakeGameResult),
        ]

    @property
    def version(self) -> int:
        return int(self._lib.snake_version())

    def choose_move(
        self,
        width: int,
        height: int,
        snake: list[tuple[int, int]],
        food: tuple[int, int] | None,
        grow: bool,
        seed: int,
        decision_id: int,
        node_budget: int,
        time_budget: float,
        food_samples: int,
        manhattan_weight: float,
        step_reward: float,
        turn_reward: float,
        food_reward: float,
        death_reward: float,
        win_reward: float,
    ) -> tuple[tuple[int, int], int] | None:
        """Mirror the old snapshot_native.choose_move(...) signature so the
        pure-Python SnakeGame can swap this in drop-in. Returns (move, depth)
        or None when no safe move exists."""
        cfg = build_config(
            width=width, height=height, initial_length=len(snake),
            food_samples=food_samples, search_nodes=node_budget,
            time_budget=time_budget, time_budget_max=time_budget,
            ramp_start=0.75, manhattan_weight=manhattan_weight,
            step_reward=step_reward, turn_reward=turn_reward,
            food_reward=food_reward, death_reward=death_reward,
            win_reward=win_reward,
        )
        cells = (ctypes.c_int * len(snake))(
            *(y * width + x for x, y in snake)
        )
        food_cell = -1 if food is None else food[1] * width + food[0]
        out_x = ctypes.c_int(0)
        out_y = ctypes.c_int(0)
        out_depth = ctypes.c_int(0)
        rc = self._lib.snake_choose_move(
            ctypes.byref(cfg),
            cells,
            len(snake),
            food_cell,
            1 if grow else 0,
            ctypes.c_uint64(seed & 0xFFFFFFFFFFFFFFFF),
            int(decision_id),
            int(node_budget),
            float(time_budget),
            ctypes.byref(out_x),
            ctypes.byref(out_y),
            ctypes.byref(out_depth),
        )
        if rc != 0:
            return None
        return (out_x.value, out_y.value), out_depth.value

    def play_game(self, cfg: SnakeGameConfig, game_seed: int, max_ticks: int) -> SnakeGameResult:
        out = SnakeGameResult()
        rc = self._lib.snake_play_game(
            ctypes.byref(cfg), ctypes.c_uint64(game_seed & 0xFFFFFFFFFFFFFFFF),
            int(max_ticks), ctypes.byref(out),
        )
        if rc != 0:
            raise RuntimeError("snake_play_game failed")
        return out


@lru_cache(maxsize=1)
def load_native(so_path: Path = DEFAULT_SO) -> SnakeNative:
    return SnakeNative(so_path)


def native_available(so_path: Path = DEFAULT_SO) -> bool:
    try:
        load_native(so_path)
        return True
    except Exception:
        return False
