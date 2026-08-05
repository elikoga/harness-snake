// Pure-JS mirror of the canonical snakecore C engine (csrc/core.c). No native
// code, no WASM — a faithful behavioral port so JS keeps working with zero
// runtime support, exactly like the pure-Python fallback. The parity target is
// the C core (NOT the Python heritage): same splitmix64/xorshift64* PRNG (via
// BigInt for exact 64-bit math), same occupancy/apply_move semantics, same
// node-budget expectimax, so chooseMove/playGame return identical decisions.
//
// Both public functions match the native/wasm backends' signatures so the
// unified facade (js/index.mjs) can swap them in.

const SEARCH_DEPTH_MAX = 512;
const SEARCH_DEPTH_FACTOR = 4;
const FOOD_SAMPLES_MAX = 64;
const MAX_CELLS = 1048576;

// ---- deterministic PRNG (mirrors core.c) ----
let rngState = 0n;

function splitmix64(x0) {
  // C truncates x += C to uint64 before use; JS BigInt does not.
  x0 = (x0 + 0x9E3779B97F4A7C15n) & 0xFFFFFFFFFFFFFFFFn;
  let z = x0;
  z = (z ^ (z >> 30n)) * 0xBF58476D1CE4E5B9n & 0xFFFFFFFFFFFFFFFFn;
  z = (z ^ (z >> 27n)) * 0x94D049BB133111EBn & 0xFFFFFFFFFFFFFFFFn;
  return (z ^ (z >> 31n)) & 0xFFFFFFFFFFFFFFFFn;
}

function prng() {
  rngState ^= rngState >> 12n;
  rngState = (rngState ^ (rngState << 25n)) & 0xFFFFFFFFFFFFFFFFn; // truncate at <<25
  rngState ^= rngState >> 27n;
  return (rngState * 0x2545F4914F6CDD1Dn) & 0xFFFFFFFFFFFFFFFFn;
}

function prngRange(n) {
  return n <= 1 ? 0 : Number(prng() % BigInt(n));
}

// ---- state ----
class State {
  constructor(w, h, cells, root_bits, nwords) {
    this.w = w; this.h = h;
    this.len = cells.length; this.root_len = cells.length;
    this.path_len = cells.length; this.plen = 0;
    this.cells = cells; // root snake head->tail (numbers)
    this.root_bits = root_bits; // array of BigInt occupancy words
    this.nwords = nwords;
    this.food = -1;
    this.paths = []; // active-length search line; length === plen, copied per child
  }
}

function rootBit(s, c) {
  return (s.root_bits[c >> 6] >> BigInt(c & 63) & 1n) === 1n;
}

function occ(s, c) {
  for (let i = 0; i < s.plen; i++) if (s.paths[i] === c) return true;
  if (s.path_len < s.root_len) {
    for (let k = s.path_len; k < s.root_len; k++) if (s.cells[k] === c) return false;
  }
  return rootBit(s, c);
}

function headCell(s) { return s.plen ? s.paths[0] : s.cells[0]; }
function secondCell(s) {
  if (s.plen >= 2) return s.paths[1];
  if (s.plen === 1) return s.cells[0];
  return s.cells[1];
}
function tailCell(s) {
  if (s.path_len > 0) return s.cells[s.path_len - 1];
  return s.paths[s.plen - 1];
}
function neighbours(s, c, out) {
  const x = c % s.w, y = Math.floor(c / s.w);
  let n = 0;
  if (y > 0) out[n++] = c - s.w;
  if (y + 1 < s.h) out[n++] = c + s.w;
  if (x > 0) out[n++] = c - 1;
  if (x + 1 < s.w) out[n++] = c + 1;
  return n;
}
function manhattan(s, a, b) {
  return Math.abs((a % s.w) - (b % s.w)) + Math.abs((Math.floor(a / s.w)) - (Math.floor(b / s.w)));
}
function isTurn(s, move) {
  if (s.len < 2) return 0;
  const x0 = headCell(s) % s.w, y0 = Math.floor(headCell(s) / s.w);
  const sec = secondCell(s);
  const x1 = sec % s.w, y1 = Math.floor(sec / s.w);
  const straight = (y0 + (y0 - y1)) * s.w + (x0 + (x0 - x1));
  return move !== straight ? 1 : 0;
}

function childState(src) {
  const s = new State(src.w, src.h, src.cells, src.root_bits, src.nwords);
  s.len = src.len; s.root_len = src.root_len; s.path_len = src.path_len; s.plen = src.plen;
  s.food = src.food;
  s.paths = src.paths.slice(); // C does *dst = *src (struct copy incl. paths[]); JS must copy
  return s;
}

function applyMove(src, m) {
  const grow = (m === src.food);
  const dst = childState(src);
  if (grow) {
    if (occ(dst, m)) return null;
    dst.len += 1;
    dst.food = -1;
  } else {
    const tail = tailCell(src);
    if (occ(src, m) && m !== tail) return null;
    if (dst.path_len > 0) dst.path_len--;
    else { dst.plen--; dst.paths.pop(); }
    dst.food = src.food;
  }
  if (dst.plen >= SEARCH_DEPTH_MAX) return null;
  dst.paths.unshift(m); // prepend head (C: shift right, paths[0]=m, plen++)
  dst.plen += 1;
  return { dst, ate: grow };
}

// ---- budget ----
function nowSec() {
  return (typeof performance !== "undefined" && performance.now)
    ? (performance.now() + (performance.timeOrigin || 0)) / 1000
    : Date.now() / 1000;
}

class Budget {
  constructor(nodeMode, nodesLeft, deadline) {
    this.nodeMode = nodeMode; this.nodesLeft = nodesLeft; this.deadline = deadline;
  }
  ok() {
    if (this.nodeMode) { this.nodesLeft--; return this.nodesLeft > 0; }
    return nowSec() < this.deadline;
  }
}

function heuristic(s, manhattanW) {
  if (s.food < 0) return 0.0;
  return -(manhattan(s, headCell(s), s.food)) * manhattanW;
}

class Params {
  constructor(cfg) {
    this.food_samples = cfg.foodSamples;
    this.manhattan_w = cfg.manhattanWeight;
    this.step_reward = cfg.stepReward;
    this.turn_reward = cfg.turnReward;
    this.food_reward = cfg.foodReward;
    this.death_reward = cfg.deathReward;
    this.win_reward = cfg.winReward;
  }
}

function sampleFree(s, samples, out) {
  const total = s.w * s.h;
  const nfree = total - s.len;
  if (nfree * 2 >= total) {
    for (let k = 0; k < samples; k++) {
      for (;;) {
        const c = prngRange(total);
        if (occ(s, c)) continue;
        let dup = 0;
        for (let j = 0; j < k; j++) if (out[j] === c) { dup = 1; break; }
        if (!dup) { out[k] = c; break; }
      }
    }
  } else {
    let n = 0;
    for (let c = 0; c < total; c++) if (!occ(s, c)) n++;
    const scratch = new Array(n);
    let m = 0;
    for (let c = 0; c < total; c++) if (!occ(s, c)) scratch[m++] = c;
    for (let k = 0; k < samples; k++) {
      const j = k + prngRange(m - k);
      const t = scratch[k]; scratch[k] = scratch[j]; scratch[j] = t;
      out[k] = scratch[k];
    }
  }
}

function solveChance(s, depth, p, b) {
  if (!b.ok()) return heuristic(s, p.manhattan_w);
  const nfree = s.w * s.h - s.len;
  if (nfree <= 0) return p.win_reward;
  let samples = nfree < p.food_samples ? nfree : p.food_samples;
  if (samples > FOOD_SAMPLES_MAX) samples = FOOD_SAMPLES_MAX;
  const chosen = new Array(samples).fill(0);
  sampleFree(s, samples, chosen);
  let total = 0.0;
  for (let k = 0; k < samples; k++) {
    const child = childState(s);
    child.food = chosen[k];
    total += solveSearch(child, depth, -Number.MAX_VALUE, Number.MAX_VALUE, p, b);
  }
  return samples > 0 ? total / samples : p.death_reward;
}

const NEG = -1.7976931348623157e308;
const POS = 1.7976931348623157e308;

function solveSearch(s, depth, alpha, beta, p, b) {
  if (!b.ok()) return heuristic(s, p.manhattan_w);
  const nb = new Array(4).fill(0);
  const n = neighbours(s, headCell(s), nb);
  const move = new Array(4).fill(0);
  const schedule = [];
  let nm = 0;
  for (let i = 0; i < n; i++) {
    const r = applyMove(s, nb[i]);
    if (r) { move[nm] = nb[i]; schedule.push(r.dst); nm++; }
  }
  if (nm === 0) return p.death_reward;
  if (depth <= 0) return heuristic(s, p.manhattan_w);
  // order children by manhattan distance to food (selection sort, mirror C)
  for (let i = 0; i < nm; i++) {
    for (let j = i + 1; j < nm; j++) {
      if (manhattan(s, move[j], s.food) < manhattan(s, move[i], s.food)) {
        const tm = move[i]; move[i] = move[j]; move[j] = tm;
        const ts = schedule[i]; schedule[i] = schedule[j]; schedule[j] = ts;
      }
    }
  }
  let best = NEG;
  const ceiling = p.food_reward * depth + p.step_reward * depth + p.win_reward + (s.w * s.h);
  for (let i = 0; i < nm; i++) {
    let value;
    const turn = isTurn(s, move[i]) ? p.turn_reward : 0.0;
    if (schedule[i].food < 0)
      value = p.food_reward + p.step_reward + turn + solveChance(schedule[i], depth - 1, p, b);
    else
      value = p.step_reward + turn + solveSearch(schedule[i], depth - 1, alpha, beta, p, b);
    if (value > best) best = value;
    if (best > alpha) alpha = best;
    if (alpha >= beta || best >= ceiling) break;
  }
  return best;
}

function chooseMoveImpl(start, growRoot, p, b, outDepth) {
  const nb = new Array(4).fill(0);
  const n = neighbours(start, headCell(start), nb);
  const tail = tailCell(start);
  const root = [];
  for (let i = 0; i < n; i++) {
    const m = nb[i];
    const safe = growRoot ? !occ(start, m) : (!occ(start, m) || m === tail);
    if (safe) root.push(m);
  }
  if (root.length === 0) return -1;
  for (let i = 0; i < root.length; i++) {
    for (let j = i + 1; j < root.length; j++) {
      if (manhattan(start, root[j], start.food) < manhattan(start, root[i], start.food)) {
        const t = root[i]; root[i] = root[j]; root[j] = t;
      }
    }
  }
  let bestMove = root[0];
  let depth = 1;
  const depthCeiling = start.w * start.h * SEARCH_DEPTH_FACTOR;
  while (depth < depthCeiling) {
    let candMove = -1;
    let candValue = NEG;
    for (let i = 0; i < root.length; i++) {
      const r = applyMove(start, root[i]);
      if (!r) continue;
      const turn = isTurn(start, root[i]) ? p.turn_reward : 0.0;
      let value;
      if (r.dst.food < 0)
        value = p.food_reward + p.step_reward + turn + solveChance(r.dst, depth - 1, p, b);
      else
        value = p.step_reward + turn + solveSearch(r.dst, depth - 1, NEG, POS, p, b);
      if (value > candValue) { candValue = value; candMove = root[i]; }
    }
    if (candMove < 0 || !b.ok()) break;
    bestMove = candMove;
    depth++;
  }
  outDepth[0] = depth - 1;
  return bestMove;
}

function buildRootBits(w, h, cells) {
  const nwords = Math.floor((w * h + 63) / 64);
  const bits = new Array(nwords).fill(0n);
  for (const c of cells) bits[c >> 6] |= 1n << BigInt(c & 63);
  return { bits, nwords };
}

function randomFreeCell(s, seedObj) {
  const total = s.w * s.h;
  const nfree = total - s.len;
  if (nfree <= 0) return -1;
  rngState = seedObj[0];
  let pick = -1;
  if (nfree * 2 >= total) {
    do { pick = prngRange(total); } while (occ(s, pick));
  } else {
    const off = prngRange(nfree);
    let k = 0;
    for (let c = 0; c < total; c++) {
      if (occ(s, c)) continue;
      if (k === off) { pick = c; break; }
      k++;
    }
  }
  seedObj[0] = rngState & 0xFFFFFFFFFFFFFFFFn;
  return pick;
}

// ---- public API (mirror snake_choose_move) ----
export function chooseMove(cfg, snake, food, grow, seed, decisionId, nodeBudget, timeBudget) {
  const w = cfg.width, h = cfg.height;
  if (w <= 0 || h <= 0 || w * h > MAX_CELLS) return null;
  if (snake.length <= 0 || snake.length > MAX_CELLS) return null;
  if (snake.some(c => c < 0 || c >= w * h)) return null;
  const { bits, nwords } = buildRootBits(w, h, snake);
  const st = new State(w, h, snake.slice(), bits, nwords);
  st.food = (food >= 0 && food < w * h) ? food : -1;
  st.len = snake.length; st.root_len = snake.length; st.path_len = snake.length; st.plen = 0;
  rngState = splitmix64((BigInt(seed) & 0xFFFFFFFFn) ^ ((BigInt(decisionId) & 0xFFFFFFFFn) << 32n) ^ 0x9E3779B97F4A7C15n);
  const p = new Params(cfg);
  const b = new Budget(nodeBudget > 0, nodeBudget, nowSec() + timeBudget);
  const outDepth = [0];
  const move = chooseMoveImpl(st, grow ? 1 : 0, p, b, outDepth);
  if (move < 0) return null;
  return { x: move % w, y: Math.floor(move / w), depth: outDepth[0] };
}

// ---- public API (mirror snake_play_game) ----
export function playGame(cfg, gameSeed, maxTicks = 1_000_000) {
  const w = cfg.width, h = cfg.height;
  if (w <= 0 || h <= 0 || w * h > MAX_CELLS) return null;
  let initial = cfg.initialLength <= 0 ? 1 : cfg.initialLength;
  if (initial > MAX_CELLS) initial = MAX_CELLS;
  const nwords = Math.floor((w * h + 63) / 64);
  const bits = new Array(nwords).fill(0n);
  const cells = [];
  const cx = Math.floor(w / 2), cy = Math.floor(h / 2);
  let len = 0;
  for (let i = 0; i < initial && cx - i >= 0; i++) {
    const cell = cy * w + (cx - i);
    cells[len++] = cell;
    bits[cell >> 6] |= 1n << BigInt(cell & 63);
  }
  if (len === 0) { const cell = cy * w; cells.push(cell); bits[cell >> 6] |= 1n << BigInt(cell & 63); len = 1; }
  const st = new State(w, h, cells, bits, nwords);
  st.len = len; st.root_len = len; st.path_len = len; st.plen = 0;
  st.food = -1;
  const p = new Params(cfg);
  let game_rng = splitmix64(BigInt(gameSeed) & 0xFFFFFFFFFFFFFFFFn);
  const seedObj = [game_rng];
  st.food = randomFreeCell(st, seedObj);
  game_rng = seedObj[0];
  let tick = 0, foods = 0, maxlen = st.len, dead = 0, filled = 0;
  const node_mode = cfg.searchNodes > 0;
  while (tick < maxTicks) {
    tick++;
    const grow = (st.food === headCell(st));
    st.root_len = st.len; st.path_len = st.len; st.plen = 0;
    rngState = splitmix64((BigInt(gameSeed) & 0xFFFFFFFFn) ^ ((BigInt(tick) & 0xFFFFFFFFn) << 32n) ^ 0x9E3779B97F4A7C15n);
    const b = new Budget(node_mode, cfg.searchNodes, nowSec() + cfg.timeBudget);
    let budget = cfg.timeBudget;
    if (!node_mode) {
      const rampStart = cfg.rampStart;
      const region = 1.0 - rampStart;
      const f = st.len / (w * h);
      if (f <= region) {
        const t = region > 0.0 ? f / region : 0.0;
        budget = cfg.timeBudgetMax - (cfg.timeBudgetMax - cfg.timeBudget) * t;
      } else if (f >= rampStart) {
        const t = (f - rampStart) / (1.0 - rampStart);
        budget = cfg.timeBudget + (cfg.timeBudgetMax - cfg.timeBudget) * t;
      }
    }
    b.deadline = nowSec() + budget;
    const outDepth = [0];
    const move = chooseMoveImpl(st, grow ? 1 : 0, p, b, outDepth);
    if (move < 0) { dead = 1; break; }
    if (grow) {
      if (st.len >= MAX_CELLS) break;
      for (let i = st.len; i > 0; i--) cells[i] = cells[i - 1];
      cells[0] = move;
      bits[move >> 6] |= 1n << BigInt(move & 63);
      st.len += 1;
      foods++;
      seedObj[0] = game_rng;
      const nf = randomFreeCell(st, seedObj);
      game_rng = seedObj[0];
      st.food = nf;
    } else {
      const tail = cells[st.len - 1];
      bits[tail >> 6] &= ~(1n << BigInt(tail & 63));
      bits[move >> 6] |= 1n << BigInt(move & 63);
      for (let i = st.len - 1; i > 0; i--) cells[i] = cells[i - 1];
      cells[0] = move;
    }
    if (st.len > maxlen) maxlen = st.len;
    if (st.len >= w * h) { dead = 1; filled = 1; break; }
    if (st.len >= MAX_CELLS) break;
  }
  return { ticks: tick, foods, length: st.len, maxlen, dead, filled };
}

export function loadMirror() {
  return { version: 1, chooseMove, playGame };
}
