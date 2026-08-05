// Quick backend smoke for `make check`: load whatever is available and run a
// fixed decision + game, printing each backend's identity and result.
// Usage: node js/smoke.mjs [wasm-path] [so-path]
import { loadBackend } from './index.mjs';

const [, , wasmArg, soArg] = process.argv;
const cfg = {
  width: 8, height: 8, initialLength: 3, foodSamples: 3, searchNodes: 3000,
  timeBudget: 0.1, timeBudgetMax: 0.3, rampStart: 0.75, manhattanWeight: 0.05,
  stepReward: 0.1, turnReward: -0.5, foodReward: 1.0, deathReward: -10.0, winReward: 100.0,
};
const snake = [4 * 8 + 3, 4 * 8 + 2, 4 * 8 + 1];

const opts = {};
if (wasmArg) opts.wasmPath = wasmArg;
if (soArg) opts.libPath = soArg;

const be = await loadBackend(opts);
console.log(`backend: ${be.kind}  version: ${be.version}`);
console.log('chooseMove:', be.chooseMove(cfg, snake, 10, false, 42, 0, 3000, 0));
console.log('playGame :', be.playGame(cfg, 12345, 1000));
if (be.errors?.length) console.log('fallback errors:', be.errors.join(' | '));
