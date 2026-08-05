/* C self-test for the snakecore shared core. Compiles the SAME core.c and
   asserts ABI/algorithm invariants, including the exact decision produced by
   the (fixed) config layout -- this is what the JS adapters must reproduce. */
#include <stdio.h>
#include <string.h>
#include "core.h"

static int failures = 0;
#define CHECK(cond, msg) do { if (!(cond)) { failures++; \
    printf("FAIL: %s\n", msg); } } while (0)

int main(void) {
    CHECK(snake_version() == 1, "snake_version() == 1");

    SnakeGameConfig cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.width = 8; cfg.height = 8; cfg.initial_length = 3; cfg.food_samples = 3;
    cfg.search_nodes = 10;
    cfg.time_budget = 0.1; cfg.time_budget_max = 0.3; cfg.ramp_start = 0.75;
    cfg.manhattan_weight = 0.05; cfg.step_reward = 0.1; cfg.turn_reward = -0.5;
    cfg.food_reward = 1.0; cfg.death_reward = -10.0; cfg.win_reward = 100.0;

    /* head (3,4), tail (1,4); food cell 10; deterministic node-budget search.
       This exact (4,4) result is a regression anchor shared with the JS
       adapters' parity harness -- it catches config-layout/ABI drift. */
    int snake[3] = {35, 34, 33};
    int x = -1, y = -1, d = -1;
    int r = snake_choose_move(&cfg, snake, 3, 10, 0, 42, 0, 10, 0, &x, &y, &d);
    CHECK(r == 0, "choose_move returns 0");
    CHECK(x == 4 && y == 4, "choose_move -> (4,4)");
    CHECK(d == 1, "choose_move depth == 1");
    printf("choose_move -> (%d,%d) depth=%d\n", x, y, d);

    /* Full headless game: must be sane (won by filling or died), counters in
       range. The exact counts depend on search_nodes but stay reproducible. */
    SnakeGameConfig g;
    memset(&g, 0, sizeof(g));
    g.width = 8; g.height = 8; g.initial_length = 3; g.food_samples = 1;
    g.search_nodes = 3000;
    g.time_budget = 0.1; g.time_budget_max = 0.3; g.ramp_start = 0.75;
    g.manhattan_weight = 0.05; g.step_reward = 0.1; g.turn_reward = -0.5;
    g.food_reward = 1.0; g.death_reward = -10.0; g.win_reward = 100.0;
    SnakeGameResult out;
    int gr = snake_play_game(&g, 12345, 100000, &out);
    CHECK(gr == 0, "play_game returns 0");
    CHECK(out.ticks > 0, "play_game ticks > 0");
    CHECK((out.filled && !out.dead && out.length == g.width * g.height)
          || (out.dead && !out.filled), "play_game ended by win or death");
    CHECK(out.length >= 1 && out.length <= g.width * g.height, "play_game length in range");
    printf("play_game -> ticks=%d foods=%d length=%d maxlen=%d dead=%d filled=%d\n",
           out.ticks, out.foods, out.length, out.maxlen, out.dead, out.filled);

    if (failures) { printf("%d FAILURES\n", failures); return 1; }
    printf("ALL C CORE TESTS PASSED\n");
    return 0;
}
