/* harness-snake public C API.
   ABI-stable: this header is the single contract shared by the Python
   ctypes/CPython adapters, the JS Bun.dlopen/Node ffi adapters, and the
   browser WASM build. Do not add struct padding: on all supported ABIs
   (x86-64, aarch64, wasm32) these plain structs have no trailing padding
   and the field order/type are what the adapters rely on.
   Tip for future FFI/parameter search code: keep the raw fields; bringing
   in other libraries (glib) would break the plain-C contract. */
#ifndef HARNESS_SNAKE_CORE_H
#define HARNESS_SNAKE_CORE_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SNAKE_VERSION 1

/* Game-tree search rewards and gameplay knobs.  Defaults in the Python
   module mirror the harness footer exactly; callers (e.g. browser parameter
   search) may override every field. */
typedef struct {
    int width;              /* board width  in cells (>=1) */
    int height;             /* board height in cells (>=1) */
    int initial_length;     /* starting snake length (>=1) */
    int food_samples;       /* random food respawns averaged at chance nodes */
    int search_nodes;       /* node budget; 0/negative => wall-clock        */
    double time_budget;     /* per-decision wall-clock budget (seconds)     */
    double time_budget_max; /* budget ceiling used by the U-curve ramps      */
    double ramp_start;      /* fraction filled where the finale ramp begins  */
    double manhattan_weight;/* greedy Manhattan-distance bias                */
    double step_reward;     /* reward per step advanced                      */
    double turn_reward;     /* penalty (negative) per turn                   */
    double food_reward;     /* reward for eating a pellet                    */
    double death_reward;    /* large negative for dying                      */
    double win_reward;      /* reward for filling the board                  */
} SnakeGameConfig;

/* One finished headless game (mirrors SnakeGame.step semantics at full loop).
   Used by browser/JS "accelerated parameter search": run play_game thousands
   of times over weight candidates and read these counters. */
typedef struct {
    int ticks;     /* game frames played through the fatal/winning tick */
    int foods;     /* food pellets eaten */
    int length;    /* snake length the moment the game ended */
    int maxlen;    /* longest the snake reached */
    int dead;      /* 1 if it died (no safe move) */
    int filled;    /* 1 if it won by filling the board */
} SnakeGameResult;

/* Returns SNAKE_VERSION. */
int snake_version(void);

/* Single expectimax decision.
   snake is snake_len head->tail cell indices (0 = board top-left, cell = y*width+x).
   Returns 0 and sets out_x/out_y/out_depth on success;
   returns -1 when stuck/no move. */
int snake_choose_move(const SnakeGameConfig* cfg, const int* snake, int snake_len,
                      int food, int grow, uint64_t seed, int decision_id,
                      int node_budget, double time_budget,
                      int* out_x, int* out_y, int* out_depth);

/* Play a full self-moving headless game; fills *out. Returns 0 on success. */
int snake_play_game(const SnakeGameConfig* cfg, uint64_t game_seed, int max_ticks,
                    SnakeGameResult* out);

#ifdef __cplusplus
}
#endif
#endif /* HARNESS_SNAKE_CORE_H */
