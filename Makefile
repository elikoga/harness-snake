# harness-snake: one C core, three targets.
#   make native  -> build/libsnakecore.so  (Bun.dlopen / Node ffi / Python ctypes)
#   make wasm    -> build/snakecore.wasm   (browser WebAssembly)
#   make all     -> both
#   make check   -> native + wasm compile, then a round-trip smoke test

CC      ?= cc
EMCC    ?= emcc
BUILD   := build
CFLAGS  := -O2 -Wall -Wextra -fPIC

all: native wasm

# Native shared library
$(BUILD)/libsnakecore.so: csrc/core.c csrc/core.h | $(BUILD)
	$(CC) $(CFLAGS) -shared -o $@ csrc/core.c

native: $(BUILD)/libsnakecore.so

# Browser WASM module (standalone, no JS glue; instantiate via WebAssembly).
$(BUILD)/snakecore.wasm: csrc/core.c csrc/core.h | $(BUILD)
	$(EMCC) -O2 csrc/core.c -o $@ \
		-sSTANDALONE_WASM=1 --no-entry \
		-sSTACK_SIZE=4194304 -sINITIAL_MEMORY=67108864 \
		-sEXPORTED_FUNCTIONS=_snake_version,_snake_choose_move,_snake_play_game,_malloc,_free \
		-sEXPORTED_RUNTIME_METHODS=ccall,cwrap,HEAPU8,HEAP32,HEAPF64,_malloc,_free

wasm: $(BUILD)/snakecore.wasm

$(BUILD):
	mkdir -p $(BUILD)

# Node runtime used to drive the JS backends / parity harness. Pass
#   make parity NODE="<node26>/bin/node --experimental-ffi"
# to also exercise the node:ffi native backend (Node >=26.1 with libffi).
NODE ?= node

# Smoke: load whatever backend is available in this runtime and run a fixed
# decision + game (bun-native / node-native / wasm / mirror all agree).
check: native wasm
	$(NODE) js/smoke.mjs build/snakecore.wasm build/libsnakecore.so

# Cross-backend parity: chooseMove + playGame must match bit-for-bit across
# every backend this Node build can load (mirror + wasm always; node-native
# when --experimental-ffi is available). Set NODE to a node26 build to include it.
parity: native wasm
	$(NODE) js/parity.mjs

# Self-check of the shared library under any runtime that can link it.
test: native
	$(CC) $(CFLAGS) -Icsrc -o $(BUILD)/coretest test/c_core_test.c csrc/core.c
	./$(BUILD)/coretest

clean:
	rm -rf $(BUILD) build/test/test_core.c

.PHONY: all native wasm check parity test clean
