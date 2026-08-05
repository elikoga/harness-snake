// WASM backend for the snakecore C core: works in browsers AND Node with zero
// dependencies (WebAssembly.instantiate + the Emscripten standalone exports).
// Struct layouts MUST match csrc/core.h and the snakecore.wasm build flags in
// the Makefile (snake_choose_move/snake_play_game/malloc/free/HEAP32/...).
//
// The core.c time-budget path calls clock_gettime(CLOCK_MONOTONIC), which the
// standalone wasm build exposes as a single WASI import (clock_time_get). We
// satisfy it with a tiny zero-dependency stub so the module stays usable in a
// plain browser page with no WASI runtime. time source defaulted to
// performance.now() where available (sub-ms, matches a real monotonic clock),
// falling back to Date.now().

const CONFIG_SIZE = 96;  // SnakeGameConfig
const RESULT_SIZE = 24;  // SnakeGameResult (6 x int32)

function nowNs() {
  const ms = (typeof performance !== "undefined" && performance.now)
    ? performance.now() + performance.timeOrigin
    : Date.now();
  return BigInt(Math.round(ms * 1e6));
}

// Build the WASI import object. `state.memory` is populated right after
// instantiation (emscripten exports `memory`), before any core call is made.
function createImports(state) {
  return {
    wasi_snapshot_preview1: {
      // errno_t clock_time_get(clockid_t id, timestamp_t precision, timestamp_t* time)
      // wasm32 ABI: (i32, i64, i32) -> i32 ; writes an i64 ns timestamp at *time.
      clock_time_get(_id, _precision, timePtr) {
        const view = new DataView(state.memory.buffer);
        view.setBigUint64(timePtr, nowNs(), true);
        return 0; // __WASI_ERRNO_SUCCESS
      },
    },
  };
}

// ---- struct layout helpers (mirror core.h offsets) ----
// SnakeGameConfig: 5 ints at 0..19, pad 20..23, 9 doubles at 24..88.
function writeConfig(view, buf, c) {
  let o = 0;
  for (const x of [c.width, c.height, c.initialLength, c.foodSamples, c.searchNodes ?? 0])
    view.setInt32(buf + (o += 4) - 4, x, true);
  o += 4; // padding: 5 ints (20B) -> doubles at 24 (core.h)
  for (const x of [c.timeBudget, c.timeBudgetMax, c.rampStart, c.manhattanWeight,
                   c.stepReward, c.turnReward, c.foodReward, c.deathReward, c.winReward]) {
    view.setFloat64(buf + (o += 8) - 8, x, true);
  }
}

export async function loadWasm(wasmPath) {
  const bytes = await (typeof wasmPath === "string"
    ? (await import("node:fs/promises")).readFile(wasmPath)
    : wasmPath);
  const state = { memory: null };
  const { instance } = await WebAssembly.instantiate(bytes, createImports(state));
  state.memory = instance.exports.memory;
  const e = instance.exports;
  const heap = () => new Int32Array(e.memory.buffer);
  const view = () => new DataView(e.memory.buffer);

  function chooseMove(cfg, snake, food, grow, seed, decisionId, nodeBudget, timeBudget) {
    const cbuf = e.malloc(CONFIG_SIZE);
    const cells = e.malloc(snake.length * 4);
    const ox = e.malloc(12); // 3 ints
    // config
    writeConfig(view(), cbuf, cfg);
    // snake cells (head->tail)
    const h = heap();
    snake.forEach((c, i) => h[(cells >> 2) + i] = c);
    // outputs zeroed
    h[(ox >> 2)] = 0; h[(ox >> 2) + 1] = 0; h[(ox >> 2) + 2] = 0;
    const r = e.snake_choose_move(cbuf, cells, snake.length, food, grow ? 1 : 0,
      BigInt(seed), decisionId ?? 0, nodeBudget ?? 0, timeBudget ?? 0, ox, ox + 4, ox + 8);
    const out = r === 0
      ? { x: h[(ox >> 2)], y: h[(ox >> 2) + 1], depth: h[(ox >> 2) + 2] }
      : null;
    e.free(cbuf); e.free(cells); e.free(ox);
    return out;
  }

  function playGame(cfg, seed, maxTicks = 1_000_000) {
    const cbuf = e.malloc(CONFIG_SIZE);
    const out = e.malloc(RESULT_SIZE);
    writeConfig(view(), cbuf, cfg);
    const h = heap();
    h[(out >> 2)] = 0; h[(out >> 2) + 1] = 0; h[(out >> 2) + 2] = 0;
    h[(out >> 2) + 3] = 0; h[(out >> 2) + 4] = 0; h[(out >> 2) + 5] = 0;
    const r = e.snake_play_game(cbuf, BigInt(seed), maxTicks, out);
    const res = r === 0 ? {
      ticks: h[(out >> 2)], foods: h[(out >> 2) + 1], length: h[(out >> 2) + 2],
      maxlen: h[(out >> 2) + 3], dead: h[(out >> 2) + 4], filled: h[(out >> 2) + 5],
    } : null;
    e.free(cbuf); e.free(out);
    return res;
  }

  return { version: e.snake_version(), chooseMove, playGame };
}
