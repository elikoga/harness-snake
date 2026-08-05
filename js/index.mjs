// Unified JS facade: auto-selects the fastest available backend for the snake.
//   Bun native (bun:ffi)      -> fastest server path
//   Node native (node:ffi)    -> plain-Node 26+ fast path (--experimental-ffi)
//   WASM (WebAssembly)        -> browser + any Node
//   Pure-JS mirror            -> zero-dependency fallback (always works)
// Every backend exposes { version, chooseMove, playGame } with identical
// signatures, and node-budget mode is bit-for-bit reproducible across all of
// them (verified by js/parity.mjs).
import { loadMirror } from './mirror.mjs';
import { loadWasm } from './wasm.mjs';

// Default build outputs resolve relative to the current working directory
// (Makefile and tests run from the project root). Browsers / embedders pass
// explicit libPath/wasmPath. import.meta.url is intentionally NOT used:
// under Bun it does not reliably point at this script.
const DEFAULT_SO =
  (typeof process !== 'undefined' && typeof process.cwd === 'function')
    ? process.cwd() + '/build/libsnakecore.so'
    : '../build/libsnakecore.so';
const DEFAULT_WASM =
  (typeof process !== 'undefined' && typeof process.cwd === 'function')
    ? process.cwd() + '/build/snakecore.wasm'
    : '../build/snakecore.wasm';

// Which backends could possibly be loadable in this runtime (cheap checks).
export function available() {
  const kinds = ['mirror']; // always available
  if (typeof Bun !== 'undefined' && Bun.dlopen) kinds.push('bun-native');
  if (process?.versions?.node) kinds.push('node-native', 'wasm');
  return kinds;
}

export async function loadBackend({ libPath, wasmPath } = {}) {
  const so = libPath || DEFAULT_SO;
  const wm = wasmPath || DEFAULT_WASM;
  const errors = [];

  // 1) Bun native: importing ./native.mjs pulls in bun:ffi, which only
  //    resolves under Bun, so the try/catch self-selects the runtime.
  try {
    const { loadNative } = await import('./native.mjs');
    const be = loadNative(so);
    if (be) { be.kind = 'bun-native'; return be; }
  } catch (e) { errors.push(`bun-native: ${e.message}`); }
  // 2) Node native (node:ffi; Node >= 26.1 with --experimental-ffi)
  try {
    const { loadNodeNative } = await import('./node-native.mjs');
    const be = loadNodeNative(so);
    if (be) { be.kind = 'node-native'; return be; }
  } catch (e) { errors.push(`node-native: ${e.message}`); }
  // 3) WASM (browser + Node, zero deps)
  try {
    const be = await loadWasm(wm);
    be.kind = 'wasm'; return be;
  } catch (e) { errors.push(`wasm: ${e.message}`); }
  // 4) Pure-JS mirror
  const be = loadMirror();
  be.kind = 'mirror';
  be.errors = errors;
  return be;
}

export { loadMirror, loadWasm };
