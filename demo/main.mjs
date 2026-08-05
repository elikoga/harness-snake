// Browser demo: animate the canonical snakecore parameter search in the browser.
// Loads the real ./build/snakecore.wasm (C core) to report its version + verify
// the core result, and uses js/mirror.mjs playGameTrace (bit-for-bit identical
// to that core) to animate the deterministic per-tick game on a <canvas>.
import { loadWasm } from '../js/wasm.mjs';
import { playGameTrace } from '../js/mirror.mjs';

const cfg = {
  width: 18, height: 14, initialLength: 3, foodSamples: 2, searchNodes: 1500,
  timeBudget: 0.05, timeBudgetMax: 0.2, rampStart: 0.7, manhattanWeight: 0.05,
  stepReward: 0.1, turnReward: -0.5, foodReward: 1.0, deathReward: -10.0,
  winReward: 100.0,
};
const BLOCK = 28;   // px per cell
const FPS = 30;     // animation frames / second

const seedEl = document.getElementById('seed');
const infoEl = document.getElementById('info');
const canvas = document.getElementById('board');
const ctx = canvas.getContext('2d');
canvas.width = cfg.width * BLOCK;
canvas.height = cfg.height * BLOCK;
let running = false;

function draw(trace, tick) {
  ctx.fillStyle = '#0e1116';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = '#1c2530';
  ctx.lineWidth = 1;
  for (let x = 0; x <= cfg.width; x++) {
    ctx.beginPath(); ctx.moveTo(x * BLOCK, 0); ctx.lineTo(x * BLOCK, canvas.height); ctx.stroke();
  }
  for (let y = 0; y <= cfg.height; y++) {
    ctx.beginPath(); ctx.moveTo(0, y * BLOCK); ctx.lineTo(canvas.width, y * BLOCK); ctx.stroke();
  }
  if (!trace || tick <= 0) return;
  const body = trace.bodies[Math.min(tick - 1, trace.bodies.length - 1)] || [];
  const food = trace.foodTrace[Math.min(tick - 1, trace.foodTrace.length - 1)];
  if (food >= 0) {
    ctx.fillStyle = '#ff4b4b';
    const fx = (food % cfg.width) * BLOCK, fy = Math.floor(food / cfg.width) * BLOCK;
    ctx.beginPath(); ctx.arc(fx + BLOCK / 2, fy + BLOCK / 2, BLOCK * 0.32, 0, Math.PI * 2); ctx.fill();
  }
  for (let i = 0; i < body.length; i++) {
    const x = (body[i] % cfg.width) * BLOCK, y = Math.floor(body[i] / cfg.width) * BLOCK;
    ctx.fillStyle = i === 0 ? '#4cf25a' : '#0a8f2f';
    ctx.fillRect(x + 1, y + 1, BLOCK - 2, BLOCK - 2);
  }
  const s = trace.summary;
  infoEl.textContent =
    `seed ${trace.seed}  |  wasm v${trace.wasmVersion ?? '?'}  |  ` +
    `tick ${Math.min(tick, s.ticks)}/${s.ticks}  foods ${s.foods}  ` +
    `length ${body.length}  maxlen ${s.maxlen}  ` +
    (s.dead ? (s.filled ? '✦ WON — board filled' : 'died') : 'alive');
}

async function playOnce() {
  if (running) return;
  running = true;
  const seed = parseInt(seedEl.value || '1', 10);
  const trace = playGameTrace(cfg, seed, 200000);
  try {
    const bytes = await (await fetch('../build/snakecore.wasm')).arrayBuffer();
    const wasm = await loadWasm(bytes);
    const coreRes = wasm.playGame(cfg, seed, 200000);
    trace.wasmVersion = wasm.version;
    console.log('wasm core result', coreRes, '| mirror trace', trace.summary,
                '| parity', JSON.stringify(coreRes) === JSON.stringify(trace.summary));
  } catch (e) {
    trace.wasmVersion = 'n/a';
    console.warn('serve from the repo root so ../build/snakecore.wasm is reachable:', e);
  }
  for (let t = 1; t <= trace.moves.length; t++) {
    draw(trace, t);
    await new Promise(r => setTimeout(r, 1000 / FPS));
  }
  draw(trace, trace.moves.length);
  running = false;
}

document.getElementById('play').addEventListener('click', playOnce);
seedEl.addEventListener('keydown', e => { if (e.key === 'Enter') playOnce(); });
infoEl.textContent = 'Pick a seed and press Play.';
