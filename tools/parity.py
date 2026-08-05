#!/usr/bin/env python3
"""Cross-language parity: drive the canonical C core (build/libsnakecore.so)
through ctypes and assert it reproduces byte-for-byte the fixture produced by
the SAME core through the JS WASM binding (build/parity-fixture.json).

This proves the two language bindings point at one identical deterministic
implementation: chooseMove (single decision) and a whole playGame (same food
RNG + search) must match exactly across JS->WASM and Python->ctypes. Run:

  node js/dump-fixture.mjs      # write build/parity-fixture.json (JS WASM)
  python3 tools/parity.py       # check it via Python ctypes
"""
from __future__ import annotations

import ctypes
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from harness_snake.native import (  # noqa: E402
    DEFAULT_SO,
    SnakeGameConfig,
    SnakeGameResult,
    SnakeNative,
)

FIXTURE = ROOT / "build" / "parity-fixture.json"


def cfg_from_dict(c) -> SnakeGameConfig:
    return SnakeGameConfig(
        c["width"], c["height"], c["initialLength"], c["foodSamples"],
        c["searchNodes"], 0,  # 0 = 4-byte pad -> doubles at offset 24
        c["timeBudget"], c["timeBudgetMax"], c["rampStart"], c["manhattanWeight"],
        c["stepReward"], c["turnReward"], c["foodReward"], c["deathReward"],
        c["winReward"],
    )


def choose(native: SnakeNative, d: dict) -> dict:
    cfg = d["cfg"]
    cells = (ctypes.c_int * len(d["snake"]))(*d["snake"])
    out_x, out_y, out_depth = ctypes.c_int(0), ctypes.c_int(0), ctypes.c_int(0)
    rc = native._lib.snake_choose_move(
        ctypes.byref(cfg_from_dict(cfg)), cells, len(d["snake"]),
        d["food"], 1 if d["grow"] else 0,
        ctypes.c_uint64(d["seed"] & 0xFFFFFFFFFFFFFFFF),
        int(d["decision"]), int(cfg["searchNodes"]), float(0),
        ctypes.byref(out_x), ctypes.byref(out_y), ctypes.byref(out_depth),
    )
    assert rc == 0, f"snake_choose_move failed rc={rc}"
    return {"x": out_x.value, "y": out_y.value, "depth": out_depth.value}


def play(native: SnakeNative, g: dict) -> dict:
    out = SnakeGameResult()
    rc = native._lib.snake_play_game(
        ctypes.byref(cfg_from_dict(g["cfg"])),
        ctypes.c_uint64(g["seed"] & 0xFFFFFFFFFFFFFFFF),
        int(g["maxTicks"]), ctypes.byref(out),
    )
    assert rc == 0, f"snake_play_game failed rc={rc}"
    return {"ticks": out.ticks, "foods": out.foods, "length": out.length,
            "maxlen": out.maxlen, "dead": out.dead, "filled": out.filled}


def main() -> int:
    if not FIXTURE.is_file():
        print(f"missing fixture: {FIXTURE} (run: node js/dump-fixture.mjs)", file=sys.stderr)
        return 2
    if not DEFAULT_SO.is_file():
        print(f"missing shared library: {DEFAULT_SO} (run: make native)", file=sys.stderr)
        return 2

    native = SnakeNative(DEFAULT_SO)
    fixture = json.loads(FIXTURE.read_text())
    assert native.version == fixture["version"], (
        f"version mismatch: native={native.version} fixture={fixture['version']}"
    )
    failures = 0

    for i, d in enumerate(fixture["decisions"]):
        expect = d["result"]
        got = None if expect is None else choose(native, d)
        if got != expect:
            failures += 1
            print(f"  chooseMove MISMATCH #{i}: fixture={expect} ctypes={got}")

    for i, g in enumerate(fixture["games"]):
        expect = g["result"]
        got = play(native, g)
        if got != expect:
            failures += 1
            for k in sorted(set(expect) | set(got)):
                if expect.get(k) != got.get(k):
                    print(f"  playGame MISMATCH #{i} {k}: fixture={expect.get(k)} ctypes={got.get(k)}")

    print(f"version={native.version} decisions={len(fixture['decisions'])} games={len(fixture['games'])}")
    if failures:
        print(f"PARITY FAILURES: {failures}")
        return 1
    print("PARITY OK: Python ctypes reproduces the canonical C core bit-for-bit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
