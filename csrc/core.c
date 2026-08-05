/* harness-snake core: the expectimax search plus the full headless game loop.
   Pure C11, no Python. Compiles unchanged to:
     - a native shared library (gcc/clang)  -> Bun.dlopen / Node node:ffi / Python ctypes
     - a WASM module (clang --target=wasm32) -> browsers
   Public API in core.h; everything else here is internal. */

#include "core.h"
#include <stdlib.h>
#include <time.h>
#include <float.h>
#include <string.h>

#ifndef SNAKE_STATIC
#define SNAKE_STATIC static
#endif

/* Board ceilings (shared with the Python/native module). */
#define MAX_CELLS 1048576
#define MAX_SNAKE MAX_CELLS
#define MAX_WORDS ((MAX_CELLS + 63) / 64)
#define SEARCH_DEPTH_MAX 512
#define SEARCH_DEPTH_FACTOR 4
#define FOOD_SAMPLES_MAX 64

/* ---- deterministic PRNG (xorshift64*), seeded per decision ---- */
SNAKE_STATIC uint64_t rng_state;
SNAKE_STATIC uint64_t splitmix64(uint64_t x) {
    uint64_t z = (x += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}
SNAKE_STATIC inline uint64_t prng(void) {
    rng_state ^= rng_state >> 12;
    rng_state ^= rng_state << 25;
    rng_state ^= rng_state >> 27;
    return rng_state * 0x2545F4914F6CDD1DULL;
}
SNAKE_STATIC inline long prng_range(long n) {
    return n <= 1 ? 0 : (long)(prng() % (uint64_t)n);
}

/* ---- state ---- */
typedef struct {
    int w, h;
    int len;
    int food;
    int path_len;
    int root_len;
    int plen;
    const uint16_t* cells;
    const uint64_t* root_bits;
    int nwords;
    int paths[SEARCH_DEPTH_MAX];
} State;

SNAKE_STATIC inline int root_bit(const State* s, int c) {
    return (int)((s->root_bits[c >> 6] >> (c & 63)) & 1ULL);
}
SNAKE_STATIC inline int occ(const State* s, int c) {
    for (int i = 0; i < s->plen; i++)
        if (s->paths[i] == c) return 1;
    if (s->path_len < s->root_len)
        for (int k = s->path_len; k < s->root_len; k++)
            if (s->cells[k] == c) return 0;
    return root_bit(s, c);
}
SNAKE_STATIC inline int head_cell(const State* s) {
    return s->plen ? s->paths[0] : (int)s->cells[0];
}
SNAKE_STATIC inline int second_cell(const State* s) {
    if (s->plen >= 2) return s->paths[1];
    if (s->plen == 1) return s->cells[0];
    return s->cells[1];
}
SNAKE_STATIC inline int tail_cell(const State* s) {
    if (s->path_len > 0) return s->cells[s->path_len - 1];
    return s->paths[s->plen - 1];
}
SNAKE_STATIC int neighbours(const State* s, int c, int* out) {
    int x = c % s->w, y = c / s->w, n = 0;
    if (y > 0) out[n++] = c - s->w;
    if (y + 1 < s->h) out[n++] = c + s->w;
    if (x > 0) out[n++] = c - 1;
    if (x + 1 < s->w) out[n++] = c + 1;
    return n;
}
SNAKE_STATIC inline int manhattan(const State* s, int a, int b) {
    return abs(a % s->w - b % s->w) + abs(a / s->w - b / s->w);
}
SNAKE_STATIC int is_turn(const State* s, int move) {
    if (s->len < 2) return 0;
    int x0 = head_cell(s) % s->w, y0 = head_cell(s) / s->w;
    int sec = second_cell(s);
    int x1 = sec % s->w, y1 = sec / s->w;
    int straight = (y0 + (y0 - y1)) * s->w + (x0 + (x0 - x1));
    return move != straight;
}
SNAKE_STATIC int apply_move(const State* src, int m, State* dst, int* ate) {
    int grow = (m == src->food);
    *dst = *src;
    if (grow) {
        if (occ(dst, m)) return 0;
        dst->len += 1;
        dst->food = -1;
    } else {
        int tail = tail_cell(src);
        if (occ(src, m) && m != tail) return 0;
        if (dst->path_len > 0) dst->path_len--;
        else dst->plen--;
        dst->food = src->food;
    }
    if (dst->plen >= SEARCH_DEPTH_MAX) return 0;
    for (int i = dst->plen; i > 0; i--) dst->paths[i] = dst->paths[i - 1];
    dst->paths[0] = m;
    dst->plen += 1;
    *ate = grow;
    return 1;
}

/* ---- budget ---- */
SNAKE_STATIC double now_sec(void) {
    return (double)clock() / (double)CLOCKS_PER_SEC;
}
typedef struct { int node_mode; int nodes_left; double deadline; } Budget;
SNAKE_STATIC int budget_ok(Budget* b) {
    if (b->node_mode) { b->nodes_left--; return b->nodes_left > 0; }
    return now_sec() < b->deadline;
}

SNAKE_STATIC double heuristic(const State* s, double manhattan_w) {
    if (s->food < 0) return 0.0;
    return -(double)manhattan(s, head_cell(s), s->food) * manhattan_w;
}

typedef struct {
    int food_samples;
    double manhattan_w, step_reward, turn_reward, food_reward, death_reward;
    double win_reward;
} Params;

SNAKE_STATIC void sample_free(const State* s, int samples, int* out) {
    int total = s->w * s->h;
    int nfree = total - s->len;
    if (nfree * 2 >= total) {
        for (int k = 0; k < samples; k++) {
            for (;;) {
                int c = (int)prng_range(total);
                if (occ(s, c)) continue;
                int dup = 0;
                for (int j = 0; j < k; j++) if (out[j] == c) { dup = 1; break; }
                if (!dup) { out[k] = c; break; }
            }
        }
    } else {
        int n = 0;
        for (int c = 0; c < total; c++) if (!occ(s, c)) n++;
        static uint16_t* scratch = NULL;
        static int scratch_cap = 0;
        if (scratch_cap < n) {
            uint16_t* np = (uint16_t*)realloc(scratch, (size_t)n * sizeof(uint16_t));
            if (!np) { scratch = NULL; scratch_cap = 0; return; }
            scratch = np; scratch_cap = n;
        }
        int m = 0;
        for (int c = 0; c < total; c++) if (!occ(s, c)) scratch[m++] = (uint16_t)c;
        for (int k = 0; k < samples; k++) {
            int j = k + (int)prng_range(m - k);
            int t = scratch[k]; scratch[k] = scratch[j]; scratch[j] = t;
            out[k] = scratch[k];
        }
    }
}

SNAKE_STATIC double solve_chance(const State* s, int depth, const Params* p, Budget* b);
SNAKE_STATIC double solve_search(const State* s, int depth, double alpha, double beta,
                                 const Params* p, Budget* b);

SNAKE_STATIC double solve_chance(const State* s, int depth, const Params* p, Budget* b) {
    if (!budget_ok(b)) return heuristic(s, p->manhattan_w);
    int nfree = s->w * s->h - s->len;
    if (nfree <= 0) return p->win_reward;
    int samples = nfree < p->food_samples ? nfree : p->food_samples;
    if (samples > FOOD_SAMPLES_MAX) samples = FOOD_SAMPLES_MAX;
    int chosen[FOOD_SAMPLES_MAX];
    sample_free(s, samples, chosen);
    double total = 0.0;
    for (int k = 0; k < samples; k++) {
        State child = *s;
        child.food = chosen[k];
        total += solve_search(&child, depth, -DBL_MAX, DBL_MAX, p, b);
    }
    return samples > 0 ? total / (double)samples : p->death_reward;
}

SNAKE_STATIC double solve_search(const State* s, int depth, double alpha, double beta,
                                 const Params* p, Budget* b) {
    if (!budget_ok(b)) return heuristic(s, p->manhattan_w);
    int nb[4], n = neighbours(s, head_cell(s), nb);
    int move[4], nm = 0;
    State child[4];
    for (int i = 0; i < n; i++) {
        State c; int ate;
        if (apply_move(s, nb[i], &c, &ate)) { move[nm] = nb[i]; child[nm] = c; nm++; }
    }
    if (nm == 0) return p->death_reward;
    if (depth <= 0) return heuristic(s, p->manhattan_w);
    for (int i = 0; i < nm; i++)
        for (int j = i + 1; j < nm; j++)
            if (manhattan(s, move[j], s->food) < manhattan(s, move[i], s->food)) {
                int tm = move[i]; move[i] = move[j]; move[j] = tm;
                State ts = child[i]; child[i] = child[j]; child[j] = ts;
            }
    double best = -DBL_MAX;
    double ceiling = p->food_reward * (double)depth + p->step_reward * (double)depth
                     + p->win_reward + (double)(s->w * s->h);
    for (int i = 0; i < nm; i++) {
        double value;
        double turn = is_turn(s, move[i]) ? p->turn_reward : 0.0;
        if (child[i].food < 0)
            value = p->food_reward + p->step_reward + turn +
                    solve_chance(&child[i], depth - 1, p, b);
        else
            value = p->step_reward + turn +
                    solve_search(&child[i], depth - 1, alpha, beta, p, b);
        if (value > best) best = value;
        if (best > alpha) alpha = best;
        if (alpha >= beta || best >= ceiling) break;
    }
    return best;
}

SNAKE_STATIC int choose_move_impl(const State* start, int grow_root, const Params* p,
                                  Budget* b, int* out_depth) {
    int nb[4], n = neighbours(start, head_cell(start), nb);
    int tail = tail_cell(start);
    int root[4], nm = 0;
    for (int i = 0; i < n; i++) {
        int m = nb[i];
        int safe = grow_root ? !occ(start, m) : (!occ(start, m) || m == tail);
        if (safe) root[nm++] = m;
    }
    if (nm == 0) return -1;
    for (int i = 0; i < nm; i++)
        for (int j = i + 1; j < nm; j++)
            if (manhattan(start, root[j], start->food) <
                manhattan(start, root[i], start->food)) {
                int t = root[i]; root[i] = root[j]; root[j] = t;
            }
    int best_move = root[0];
    int depth = 1;
    long depth_ceiling = (long)start->w * start->h * SEARCH_DEPTH_FACTOR;
    while (depth < depth_ceiling) {
        int cand_move = -1;
        double cand_value = -DBL_MAX;
        for (int i = 0; i < nm; i++) {
            State c; int ate;
            if (!apply_move(start, root[i], &c, &ate)) continue;
            double turn = is_turn(start, root[i]) ? p->turn_reward : 0.0;
            double value;
            if (c.food < 0)
                value = p->food_reward + p->step_reward + turn +
                        solve_chance(&c, depth - 1, p, b);
            else
                value = p->step_reward + turn +
                        solve_search(&c, depth - 1, -DBL_MAX, DBL_MAX, p, b);
            if (value > cand_value) { cand_value = value; cand_move = root[i]; }
        }
        if (cand_move < 0 || !budget_ok(b)) break;
        best_move = cand_move;
        depth++;
    }
    if (out_depth) *out_depth = depth - 1;
    return best_move;
}

SNAKE_STATIC int random_free_cell(const State* s, uint64_t* seed) {
    int total = s->w * s->h;
    int nfree = total - s->len;
    if (nfree <= 0) return -1;
    rng_state = *seed;
    int pick = -1;
    if (nfree * 2 >= total) {
        do { pick = (int)prng_range(total); } while (occ(s, pick));
    } else {
        int off = (int)prng_range(nfree), k = 0;
        for (int c = 0; c < total; c++) {
            if (occ(s, c)) continue;
            if (k == off) { pick = c; break; }
            k++;
        }
    }
    *seed = rng_state;
    return pick;
}

/* ---- public API ---- */

int snake_version(void) {
    return SNAKE_VERSION;
}

int snake_choose_move(const SnakeGameConfig* cfg, const int* snake, int snake_len,
                      int food, int grow, uint64_t seed, int decision_id,
                      int node_budget, double time_budget,
                      int* out_x, int* out_y, int* out_depth) {
    int w = cfg->width, h = cfg->height;
    if (w <= 0 || h <= 0 || w * h > MAX_CELLS) return -1;
    if (snake_len <= 0 || snake_len > MAX_SNAKE) return -1;
    uint64_t* root_bits = (uint64_t*)calloc((size_t)((w * h + 63) / 64), sizeof(uint64_t));
    uint16_t* cells = (uint16_t*)malloc((size_t)snake_len * sizeof(uint16_t));
    if (!root_bits || !cells) { free(root_bits); free(cells); return -1; }
    for (int i = 0; i < snake_len; i++) {
        int idx = snake[i];
        if (idx < 0 || idx >= w * h) { free(root_bits); free(cells); return -1; }
        cells[i] = (uint16_t)idx;
        root_bits[idx >> 6] |= 1ULL << (idx & 63);
    }
    State st;
    memset(&st, 0, sizeof(st));
    st.w = w; st.h = h; st.cells = cells; st.root_bits = root_bits;
    st.nwords = (int)((w * h + 63) / 64);
    st.food = (food >= 0 && food < w * h) ? food : -1;
    st.len = snake_len; st.root_len = snake_len; st.path_len = snake_len; st.plen = 0;
    rng_state = splitmix64(((uint64_t)(uint32_t)seed)
        ^ ((uint64_t)(uint32_t)decision_id << 32) ^ 0x9E3779B97F4A7C15ULL);
    Params p;
    p.food_samples = cfg->food_samples;
    p.manhattan_w = cfg->manhattan_weight;
    p.step_reward = cfg->step_reward;
    p.turn_reward = cfg->turn_reward;
    p.food_reward = cfg->food_reward;
    p.death_reward = cfg->death_reward;
    p.win_reward = cfg->win_reward;
    Budget b;
    b.node_mode = node_budget > 0;
    b.nodes_left = node_budget;
    b.deadline = now_sec() + time_budget;
    int depth = 0;
    int move = choose_move_impl(&st, grow, &p, &b, &depth);
    free(root_bits);
    free(cells);
    if (move < 0) return -1;
    *out_x = move % w; *out_y = move / w; *out_depth = depth;
    return 0;
}

int snake_play_game(const SnakeGameConfig* cfg, uint64_t game_seed, int max_ticks,
                    SnakeGameResult* out) {
    int w = cfg->width, h = cfg->height;
    if (w <= 0 || h <= 0 || w * h > MAX_CELLS) return -1;
    int initial = cfg->initial_length <= 0 ? 1 : cfg->initial_length;
    if (initial > MAX_SNAKE) initial = MAX_SNAKE;
    int nwords = (int)((w * h + 63) / 64);
    uint64_t* root_bits = (uint64_t*)calloc((size_t)nwords, sizeof(uint64_t));
    uint16_t* cells = (uint16_t*)malloc((size_t)MAX_SNAKE * sizeof(uint16_t));
    if (!root_bits || !cells) { free(root_bits); free(cells); return -1; }
    State st;
    memset(&st, 0, sizeof(st));
    st.w = w; st.h = h; st.cells = cells; st.root_bits = root_bits; st.nwords = nwords;
    int cx = (int)(w / 2), cy = (int)(h / 2), len = 0;
    for (int i = 0; i < initial && cx - i >= 0; i++) {
        int cell = cy * w + (cx - i);
        cells[len++] = (uint16_t)cell;
        root_bits[cell >> 6] |= 1ULL << (cell & 63);
    }
    if (len == 0) { int cell = cy * w; cells[0] = (uint16_t)cell;
                    root_bits[cell >> 6] |= 1ULL << (cell & 63); len = 1; }
    st.len = len; st.root_len = len; st.path_len = len; st.plen = 0;
    st.food = -1;
    Params p;
    p.food_samples = cfg->food_samples;
    p.manhattan_w = cfg->manhattan_weight;
    p.step_reward = cfg->step_reward;
    p.turn_reward = cfg->turn_reward;
    p.food_reward = cfg->food_reward;
    p.death_reward = cfg->death_reward;
    p.win_reward = cfg->win_reward;
    uint64_t game_rng = splitmix64(game_seed);
    st.food = random_free_cell(&st, &game_rng);
    long tick = 0, foods = 0, maxlen = (long)st.len;
    int dead = 0, filled = 0;
    int node_mode = cfg->search_nodes > 0;
    while (tick < max_ticks) {
        tick++;
        int grow = (st.food == head_cell(&st));
        st.root_len = st.len; st.path_len = st.len; st.plen = 0;
        rng_state = splitmix64(((uint64_t)(uint32_t)game_seed)
            ^ ((uint64_t)(uint32_t)tick << 32) ^ 0x9E3779B97F4A7C15ULL);
        Budget b;
        b.node_mode = node_mode;
        b.nodes_left = cfg->search_nodes;
        double budget = cfg->time_budget;
        if (!node_mode) {
            double ramp_start = cfg->ramp_start;
            double region = 1.0 - ramp_start;
            double f = (double)st.len / ((double)w * (double)h);
            if (f <= region) {
                double t = region > 0.0 ? f / region : 0.0;
                budget = cfg->time_budget_max - (cfg->time_budget_max - cfg->time_budget) * t;
            } else if (f >= ramp_start) {
                double t = (f - ramp_start) / (1.0 - ramp_start);
                budget = cfg->time_budget + (cfg->time_budget_max - cfg->time_budget) * t;
            }
        }
        b.deadline = now_sec() + budget;
        int depth = 0;
        int move = choose_move_impl(&st, grow, &p, &b, &depth);
        if (move < 0) { dead = 1; break; }
        if (grow) {
            if (st.len >= MAX_SNAKE) break;
            for (int i = st.len; i > 0; i--) cells[i] = cells[i - 1];
            cells[0] = (uint16_t)move;
            root_bits[move >> 6] |= 1ULL << (move & 63);
            st.len += 1;
            foods++;
            st.food = random_free_cell(&st, &game_rng);
        } else {
            int tail = (int)cells[st.len - 1];
            root_bits[tail >> 6] &= ~(1ULL << (tail & 63));
            root_bits[move >> 6] |= 1ULL << (move & 63);
            for (int i = st.len - 1; i > 0; i--) cells[i] = cells[i - 1];
            cells[0] = (uint16_t)move;
        }
        if ((long)st.len > maxlen) maxlen = (long)st.len;
        if (st.len >= w * h) { dead = 1; filled = 1; break; }
        if (st.len >= MAX_SNAKE) break;
    }
    out->ticks = (int)tick;
    out->foods = (int)foods;
    out->length = (int)st.len;
    out->maxlen = (int)maxlen;
    out->dead = dead;
    out->filled = filled;
    free(root_bits);
    free(cells);
    return 0;
}
