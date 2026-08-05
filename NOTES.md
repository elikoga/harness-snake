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

## Python (ctypes)
- src/harness_snake/native.py dlopens build/libsnakecore.so (the same object the JS
  backends use); SnakeGameConfig/SnakeGameResult use ctypes.Structure matching the 96B/24B
  layouts. snake_choose_move/snake_play_game bound; build_config mirrors core.h incl. 4B pad.
- src/harness_snake/__init__.py swaps the old snake_native C-extension import for the ctypes
  loader (falls back to pure-Python expectimax when build/ absent).
- Fixed a latent pure-Python bug in _apply_move: `set(snake) - (tail_tuple)` ->
  `set(snake) - set(tail_tuple)` (set-tuple subtraction raised TypeError when the
  accelerator was unavailable).
- Viewer import fixed: `from snake import ...` -> `from . import ...`.
- Added minimal pyproject.toml (src layout, snake-view = harness_snake.viewer:main).

## nano-harness repoint (271 tests green)
- nano-harness/snake.py is now a thin re-export shim over harness_snake; the old
  snake.py/snake_viewer.py/snake_native.c/.so were deleted; setup.py slimmed to
  py_modules=["snake"]; pyproject snake-view script removed (harness-snake provides it).
- Installed harness-snake editable into the nano-harness venv; full suite:
  271 passed, 3 subtests. snake-view --one runs the C core (search 101/100ms).

## TODO
- [x] smoke-test js/wasm.mjs vs real wasm
- [x] Node native backend (js/node-native.mjs) via node:ffi on Node 26
- [x] pure-JS mirror fallback (js/mirror.mjs)
- [x] unified JS facade (js/index.mjs): bun-native -> node-native -> wasm -> mirror
- [x] Python ctypes adapter in src/harness_snake
- [x] repoint nano-harness at the package; drop old snake.py etc; keep 271 tests green
- [x] cross-language parity: JS mirror/wasm/node-native all agree (make parity); the
  same C core driven through Python ctypes reproduces the JS WASM fixture bit-for-bit
  (make parity-py; 30 chooseMove + 8 full playGame counters via tools/parity.py).
  Note: the pure-Python heritage _game_move_py uses a different RNG and is NOT
  bit-for-bit against the C core (~357/400 decisions match, rest tie-breaks); the
  bit-for-bit anchor is the C core + JS mirror + JS/Python bindings.
- [x] snake-view console tool ships as the package script (harness_snake.viewer:main).
- [x] browser demo (demo/index.html + demo/main.mjs): loads the real build/snakecore.wasm
  (C core) and logs its result, animates js/mirror.mjs playGameTrace (bit-for-bit identical)
  on a canvas. Run `make demo` then open /demo/index.html. playGameTrace returns per-tick
  moves/food/bodies/summary for animation (node-budget parameter search path).
