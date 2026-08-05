// Bun native FFI binding for the snakecore C library (the fastest server path).
// Struct layouts MUST match csrc/core.h (SnakeGameConfig is 10 ints + 6 doubles).
import { dlopen, ptr } from "bun:ffi";

export const CONFIG_BYTES = 96; // 10*int(4) + pad(4) + 6*double(8) = 96

export function writeConfig(buf, c) {
  const v = new DataView(buf);
  let off = 0;
  const ips = [c.width, c.height, c.initialLength, c.foodSamples, c.searchNodes ?? 0];
  for (const x of ips) { v.setInt32(off, x, true); off += 4; }
  off += 4; // padding: 5 ints (20B) -> doubles at 24 (core.h)
  const dps = [c.timeBudget, c.timeBudgetMax, c.rampStart, c.manhattanWeight,
               c.stepReward, c.turnReward, c.foodReward, c.deathReward, c.winReward];
  for (const x of dps) { v.setFloat64(off, x, true); off += 8; }
}

export function loadNative(libPath) {
  const { symbols } = dlopen(libPath, {
    snake_version: { args: [], returns: "int" },
    snake_choose_move: { args: ["ptr", "ptr", "int", "int", "int", "i64",
        "int", "int", "double", "ptr", "ptr", "ptr"], returns: "int" },
    snake_play_game: { args: ["ptr", "i64", "int", "ptr"], returns: "int" },
  });

  function chooseMove(cfg, snake, food, grow, seed, decisionId, nodeBudget, timeBudget) {
    const cbuf = new ArrayBuffer(CONFIG_BYTES);
    writeConfig(cbuf, cfg);
    const cells = new Int32Array(snake.length);
    snake.forEach((c, i) => cells[i] = c);
    const ox = new Int32Array(1), oy = new Int32Array(1), od = new Int32Array(1);
    const r = symbols.snake_choose_move(
      ptr(cbuf), ptr(cells), snake.length, food, grow ? 1 : 0,
      BigInt(seed), decisionId ?? 0, nodeBudget ?? 0, timeBudget ?? 0,
      ptr(ox), ptr(oy), ptr(od));
    if (r !== 0) return null;
    return { x: ox[0], y: oy[0], depth: od[0] };
  }

  function playGame(cfg, seed, maxTicks = 1_000_000) {
    const cbuf = new ArrayBuffer(CONFIG_BYTES);
    writeConfig(cbuf, cfg);
    const out = new ArrayBuffer(24); // SnakeGameResult: 6 ints
    const r = symbols.snake_play_game(ptr(cbuf), BigInt(seed), maxTicks, ptr(out));
    if (r !== 0) return null;
    const v = new DataView(out);
    return { ticks: v.getInt32(0, true), foods: v.getInt32(4, true),
      length: v.getInt32(8, true), maxlen: v.getInt32(12, true),
      dead: v.getInt32(16, true), filled: v.getInt32(20, true) };
  }

  return { version: symbols.snake_version(), chooseMove, playGame };
}
