// Cross-backend parity: mirror vs wasm vs node-native on identical inputs.
// Uses node-budget mode (searchNodes) so decisions are fully deterministic and
// must match byte-for-byte across every backend, most importantly chooseMove
// (single decision) and a whole playGame (identically-seeded food RNG).
import { loadMirror } from './mirror.mjs';
import { loadWasm } from './wasm.mjs';

const SO = "/home/elikoga/Dev/harness-snake/build/libsnakecore.so";
const WASM = "/home/elikoga/Dev/harness-snake/build/snakecore.wasm";

const baseCfg = {
  width: 10, height: 10, initialLength: 3, foodSamples: 3, searchNodes: 5000,
  timeBudget: 0.05, timeBudgetMax: 0.2, rampStart: 0.7, manhattanWeight: 0.05,
  stepReward: 0.1, turnReward: -0.5, foodReward: 1.0, deathReward: -10.0,
  winReward: 100.0,
};

function run(backends) {
  const n = backends.mirror.chooseMove.name;
  let failures = 0;
  // 30 single-decision cases across varied boards/seeds/positions.
  for (let t = 0; t < 30; t++) {
    const cfg = { ...baseCfg, width: 6 + (t % 6), height: 5 + (t % 5),
      searchNodes: 2000 + t * 500, foodSamples: 2 + (t % 4) };
    const w = cfg.width, h = cfg.height;
    const cx = Math.floor(w / 2), cy = Math.floor(h / 2);
    const snake = [cy * w + cx, cy * w + cx - 1, cy * w + cx - 2];
    const food = (t * 37) % (w * h);
    const seed = 1000 + t * 7, decision = t * 3;
    const grow = (t % 3) === 0;
    const ref = backends.mirror.chooseMove(cfg, snake, food, grow, seed, decision, cfg.searchNodes, 0);
    for (const [name, be] of Object.entries(backends)) {
      if (name === "mirror") continue;
      const got = be.chooseMove(cfg, snake, food, grow, seed, decision, cfg.searchNodes, 0);
      const r = JSON.stringify(ref), g = JSON.stringify(got);
      if (r !== g) {
        failures++;
        console.log(`  MISMATCH t=${t} ${name}: mirror=${r} ${name}=${g}`);
      }
    }
  }
  // A full deterministic game: play_game must be byte-identical (same food RNG).
  for (let t = 0; t < 8; t++) {
    const cfg = { ...baseCfg, width: 8 + (t % 6), height: 6 + (t % 4),
      searchNodes: 1500 + t * 400, foodSamples: 1, seed: 0 };
    const seed = 5555 + t * 13;
    const ref = backends.mirror.playGame(cfg, seed, 5000);
    for (const [name, be] of Object.entries(backends)) {
      if (name === "mirror") continue;
      const got = be.playGame(cfg, seed, 5000);
      const r = JSON.stringify(ref), g = JSON.stringify(got);
      if (r !== g) {
        failures++;
        // show a compact diff on the counters
        const rr = JSON.parse(r), gg = JSON.parse(g);
        const keys = new Set([...Object.keys(rr), ...Object.keys(gg)]);
        for (const k of keys) if (rr[k] !== gg[k]) console.log(`  game MISMATCH t=${t} ${name} ${k}: ${rr[k]} vs ${gg[k]}`);
      }
    }
  }
  console.log(failures === 0 ? "PARITY OK: all backends agree" : `PARITY FAILURES: ${failures}`);
  return failures;
}

const mirror = loadMirror();
const wasm = await loadWasm(WASM);

let nodeNative = null;
try {
  const { loadNodeNative } = await import('./node-native.mjs');
  nodeNative = loadNodeNative(SO);
} catch (e) { console.log("(node-native unavailable:", e.message, ")"); }

const backends = { mirror, wasm, ...(nodeNative ? { ["node-native"]: nodeNative } : {}) };
console.log("backends:", Object.keys(backends));
console.log("reference chooseMove:", backends.mirror.chooseMove(
  baseCfg, [4*10+5, 4*10+4, 4*10+3], 47, false, 42, 0, 5000, 0));
const rc = run(backends);
process.exit(rc ? 1 : 0);
