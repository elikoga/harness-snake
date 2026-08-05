import { createRequire } from "node:module";
// Node native FFI backend for the snakecore C library, using the official
// node:ffi module (Node >= 26.1.0, built with libffi; enable with
// --experimental-ffi). This is the plain-Node equivalent of bun:ffi
// (js/native.mjs) so the native fast path works without Bun.
// Struct layouts MUST match csrc/core.h (SnakeGameConfig = 96 bytes,
// SnakeGameResult = 24 bytes).

const CONFIG_SIZE = 96; // 5*int(4) + pad(4) + 9*double(8)
const RESULT_SIZE = 24; // 6*int(4)

export function writeConfig(buf, c) {
  const v = new DataView(buf);
  let o = 0;
  for (const x of [c.width, c.height, c.initialLength, c.foodSamples, c.searchNodes ?? 0]) {
    v.setInt32(o, x, true); o += 4;
  }
  o += 4; // padding: 5 ints (20B) -> doubles at 24 (core.h)
  for (const x of [c.timeBudget, c.timeBudgetMax, c.rampStart, c.manhattanWeight,
                   c.stepReward, c.turnReward, c.foodReward, c.deathReward, c.winReward]) {
    v.setFloat64(o, x, true); o += 8;
  }
}

export function loadNodeNative(libPath) {
  const require = createRequire(import.meta.url);
  let ffi;
  try {
    ffi = require("node:ffi");
  } catch {
    throw new Error("node:ffi is not available; run Node with --experimental-ffi (Node >= 26.1.0)");
  }
  const { functions } = ffi.dlopen(libPath, {
    snake_version: { arguments: [], return: "i32" },
    snake_choose_move: {
      arguments: ["pointer", "pointer", "i32", "i32", "i32",
        "i64", "i32", "i32", "f64", "pointer", "pointer", "pointer"],
      return: "i32",
    },
    snake_play_game: { arguments: ["pointer", "i64", "i32", "pointer"], return: "i32" },
  });

  function chooseMove(cfg, snake, food, grow, seed, decisionId, nodeBudget, timeBudget) {
    const cbuf = new ArrayBuffer(CONFIG_SIZE);
    writeConfig(cbuf, cfg);
    const cells = new Int32Array(snake.length);
    snake.forEach((c, i) => cells[i] = c);
    const ox = new ArrayBuffer(4), oy = new ArrayBuffer(4), od = new ArrayBuffer(4);
    const r = functions.snake_choose_move(
      Buffer.from(cbuf), Buffer.from(cells.buffer), snake.length, food, grow ? 1 : 0,
      BigInt(seed), decisionId ?? 0, nodeBudget ?? 0, timeBudget ?? 0,
      Buffer.from(ox), Buffer.from(oy), Buffer.from(od));
    if (r !== 0) return null;
    const dx = new DataView(ox), dy = new DataView(oy), dd = new DataView(od);
    return { x: dx.getInt32(0, true), y: dy.getInt32(0, true), depth: dd.getInt32(0, true) };
  }

  function playGame(cfg, seed, maxTicks = 1_000_000) {
    const cbuf = new ArrayBuffer(CONFIG_SIZE);
    writeConfig(cbuf, cfg);
    const out = new ArrayBuffer(RESULT_SIZE);
    const r = functions.snake_play_game(Buffer.from(cbuf), BigInt(seed), maxTicks, Buffer.from(out));
    if (r !== 0) return null;
    const v = new DataView(out);
    return { ticks: v.getInt32(0, true), foods: v.getInt32(4, true),
      length: v.getInt32(8, true), maxlen: v.getInt32(12, true),
      dead: v.getInt32(16, true), filled: v.getInt32(20, true) };
  }

  return { version: functions.snake_version(), chooseMove, playGame };
}
