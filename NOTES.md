# harness-snake progress notes

## Backends (all verified running against the same csrc/core.{c,h})
- Native .so: `build/libsnakecore.so` (cc -O2 -shared)
  - Bun:  js/native.mjs (bun:ffi)            VERIFIED
  - Node: js/node-native.mjs (node:ffi)      VERIFIED  (Node >=26.1.0, --experimental-ffi)
- WASM:  `build/snakecore.wasm` (emcc standalone)      VERIFIED
  - js/wasm.mjs includes a zero-dep WASI clock_time_get stub.

Identical smoke result across node-native + wasm:
  chooseMove -> {x:3, y:3, depth:0}
  playGame   -> {ticks:83, foods:13, length:16, maxlen:16, dead:1, filled:0}

## Node FFI
- node:ffi documented at https://nodejs.org/api/ffi.html (added v26.1.0, experimental,
  gated by --experimental-ffi; requires libffi-enabled build).
- Official v26.6.0 linux-x64 prebuilt includes it: /tmp/node26/bin/node --experimental-ffi
- nixpkgs has only nodejs_20/22/24 (no 26), so node26 is a manual tarball install for now.

## struct layouts (core.h)
SnakeGameConfig = 96 bytes = 5*int(4) @0..19, pad 20..23, 9*double(8) @24..88
SnakeGameResult = 24 bytes = 6*int(4)
snake_choose_move(cfg*, snake*, len, food, grow, seed:i64, decision, node_budget,
                  time_budget:f64, out_x*, out_y*, out_depth*) -> i32
snake_play_game(cfg*, seed:i64, max_ticks, result*) -> i32

## TODO
- [x] smoke-test js/wasm.mjs vs real wasm
- [x] Node native backend (js/node-native.mjs) via node:ffi on Node 26
- [ ] pure-JS mirror fallback (js/mirror.mjs)
- [ ] unified JS facade (js/index.mjs): bun-native -> node-native -> wasm -> mirror
- [ ] Python ctypes adapter in src/harness_snake
- [ ] repoint nano-harness at the package; drop old snake.py etc; keep 271 tests green
- [ ] cross-language parity harness (native/wasm/mirror/ctypes/pure-python)
- [ ] snake-view console tool + browser demo page (wasm parameter search)
