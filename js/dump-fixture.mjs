// Dump a cross-language parity fixture: the canonical C core's deterministic
// chooseMove + playGame results, computed through the JS WASM binding, written
// to build/parity-fixture.json. The Python tools/parity.py drives the SAME C
// core through ctypes and must reproduce these byte-for-byte.
import { writeFile } from 'node:fs/promises';
import { loadWasm } from './wasm.mjs';

const WASM = "/home/elikoga/Dev/harness-snake/build/snakecore.wasm";
const OUT  = "/home/elikoga/Dev/harness-snake/build/parity-fixture.json";

const baseCfg = {
  width: 10, height: 10, initialLength: 3, foodSamples: 3, searchNodes: 5000,
  timeBudget: 0.05, timeBudgetMax: 0.2, rampStart: 0.7, manhattanWeight: 0.05,
  stepReward: 0.1, turnReward: -0.5, foodReward: 1.0, deathReward: -10.0,
  winReward: 100.0,
};
// Same case formulas as js/parity.mjs.
const decisions = [];
for (let t = 0; t < 30; t++) {
  const cfg = { ...baseCfg, width: 6 + (t % 6), height: 5 + (t % 5),
    searchNodes: 2000 + t * 500, foodSamples: 2 + (t % 4) };
  const { width: w, height: h } = cfg;
  const cx = Math.floor(w / 2), cy = Math.floor(h / 2);
  const snake = [cy * w + cx, cy * w + cx - 1, cy * w + cx - 2];
  const food = (t * 37) % (w * h);
  const seed = 1000 + t * 7, decision = t * 3;
  const grow = (t % 3) === 0;
  decisions.push({ cfg, snake, food, grow, seed, decision, result: null });
}
const games = [];
for (let t = 0; t < 8; t++) {
  const cfg = { ...baseCfg, width: 8 + (t % 6), height: 6 + (t % 4),
    searchNodes: 1500 + t * 400, foodSamples: 1 };
  const seed = 5555 + t * 13;
  games.push({ cfg, seed, maxTicks: 5000, result: null });
}

const wasm = await loadWasm(WASM);
for (const d of decisions) {
  d.result = wasm.chooseMove(d.cfg, d.snake, d.food, d.grow, d.seed, d.decision,
                             d.cfg.searchNodes, 0);
}
for (const g of games) g.result = wasm.playGame(g.cfg, g.seed, g.maxTicks);

const fixture = { version: wasm.version, decisions, games };
await writeFile(OUT, JSON.stringify(fixture));
console.log(`fixture written: ${OUT}`);
console.log("sample chooseMove:", decisions[0].result);
console.log("sample playGame:", games[0].result);
