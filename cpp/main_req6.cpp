/* main_req6.cpp —— 实验内容 6 专用驱动：不同数据量（足够大）下的时间性能评估
 *
 * 题目要求：针对不同的数据量（足够大），对正序数据、逆序数据以及均匀分布、
 * 高斯分布、泊松分布的随机数序列运行以上算法，通过比较次数、移动次数和
 * 计算时间评估各种算法的时间性能。
 *
 * 矩阵设计（数据量尽可能大）：
 *   分布（5）：均匀 / 高斯 / 泊松 / 正序 / 逆序 —— 与全矩阵实验同一生成器
 *             （datagen.h，xorshift32 固定种子，同 (分布,规模) 逐字节可复现，
 *             且 10^4/10^5/10^6 三档与 results_c.csv 的计数可交叉验证一致）
 *   规模（5）：10^4、10^5、10^6、10^7、3.2×10^7
 *             顶档 3.2×10^7 = CineRank 的 MovieLens 全量评分规模，即应用
 *             真实承受的最大排序负载；
 *   算法（10）：8 种自实现 + AdaptSort + std::sort 基线
 *   Θ(n²) 三算法：n ≤ 10^5 全分布实跑；n > 10^5 仅正序档实跑直接插入/冒泡
 *   （正序下二者为 O(n)，恰好给出"数据形态决定 O(n²) 算法生死"的对照；
 *   其余记 -1 不实跑——冒泡 10^6 档单次估计 ~40 min，实测代价不可接受，
 *   这本身即 O(n²) 在大数据量下不可用的证据）
 *
 * 测量方法学（与 common/bench.h 完全同口径，仅两处按规模适配）：
 *   1) 计数/计时分离：计时模式 st=NULL，计数模式单独跑一遍；
 *   2) QPC 高精度计时；等时长批量（est<8ms 时整批 ~8ms 计时再除以 B，
 *      拷贝工作集 ≤ 8MB）；
 *   3) 重复取最小值为主口径（负载是单侧噪声）；轮数：常规档 5 轮，
 *      Θ(n²) 组与 n ≥ 10^7 档 3 轮（单次已达秒级~十秒级，3 轮最小值
 *      已充分稳定，且控制总时长）；
 *   4) n ≥ 3×10^6 档计时改为"固定母本逐轮 memcpy"：泊松生成每元素
 *      ~50 次随机迭代，10^7+ 规模下 6 次逐轮再生成要 ~3 分钟/单元，
 *      而测量只需"每轮拿到一份未排序的相同负载"，memcpy 即满足；
 *      母本在计时区间外生成一次。小规模档保持逐轮重新生成（与 bench.h
 *      语义一致，计数与 results_c.csv 交叉验证）。
 *
 * 输出：results/results_req6.txt + results/results_req6.csv
 * 编译：g++ -O2 -std=c++17 -Wall -I common -I cpp/adaptsort cpp/main_req6.cpp
 *       -o build/sort_req6.exe
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <ctime>
#include <algorithm>
#include <string>
#ifdef _WIN32
#include <windows.h>
#endif
#include "sortstats.h"
#include "datagen.h"
#include "sortcore.h"

#include "adaptsort.h"    /* sb::adapt_sort / strat_name / last_strategy */
#include "counters.h"     /* sb::counters / sb::mem */

using namespace sb;

/* ---------------- 算法登记表（与 main_cpp.cpp 完全一致的包装） ---------------- */
typedef void (*SortFunc)(int *a, int n, int order, SortStats *st);

typedef struct Algo {
    const char *id;
    const char *name;
    SortFunc fn;
    int quadratic;
    bool is_adapt;
} Algo;

/* ---- AdaptSort 桥接（照搬 main_cpp.cpp） ---- */
static char g_last_strat[64] = "";

static void adaptWrap(int *a, int n, int order, SortStats *st) {
    if (st) {
        counters().reset();
        counters().on = true;
        mem().reset();
    }
    adapt_sort(a, n);
    if (order == -1) {
        reverse_array(a, n);
    }
    std::string strat = strat_name(last_strategy());
    if (last_strategy() == Strat::RadixLSD && last_radix_db() == 8)
        strat += "(8位)";
    std::snprintf(g_last_strat, sizeof(g_last_strat), "%s",
                  order == -1 ? (strat + "+反转").c_str() : strat.c_str());
    if (st) {
        stats_init(st, order, "AdaptSort");
        st->compares = (long long)counters().cmp;
        st->moves    = (long long)counters().mv;
        st->aux_peak = (long long)mem().peak;
        counters().on = false;
    }
}

/* ---- std::sort 基线 ---- */
static SortStats *g_sbridge = nullptr;
static int g_sorder = 1;

struct CountLess {
    bool operator()(int x, int y) const {
        if (g_sbridge) g_sbridge->compares++;
        if (g_sorder == 1) return x < y;
        return y < x;
    }
};

static void stdSortWrap(int *a, int n, int order, SortStats *st) {
    g_sorder = order;
    g_sbridge = st;
    if (st) stats_init(st, order, "std::sort");
    std::sort(a, a + n, CountLess());
    g_sbridge = nullptr;
    if (st) { st->moves = -1; st->aux_peak = -1; }
}

static const Algo ALGOS[] = {
    { "insertion", "直接插入", insertionSort, 1, false },
    { "bubble",    "冒泡",     bubbleSort,    1, false },
    { "selection", "选择",     selectSort,    1, false },
    { "shell",     "希尔",     shellSort,     0, false },
    { "quick",     "快速",     quickSort,     0, false },
    { "heap",      "堆",       heapSort,      0, false },
    { "radix",     "基数",     radixSort,     0, false },
    { "merge",     "归并",     mergeSort,     0, false },
    { "adapt",     "AdaptSort", adaptWrap,    0, true  },
    { "stdsort",   "std::sort", stdSortWrap,  0, false },
};
#define NALGO (sizeof(ALGOS) / sizeof(ALGOS[0]))

/* ---------------- 输出 ---------------- */
static FILE *g_log;

static void bothLine(const char *s) {
    fputs(s, stdout);
    if (g_log) fputs(s, g_log);
    fflush(stdout);
}
static void bothFmt(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    if (g_log) {
        va_start(ap, fmt);
        vfprintf(g_log, fmt, ap);
        va_end(ap);
    }
    fflush(stdout);
}

/* ---------------- 基准计时（bench.h 同口径 + 轮数参数 + 母本模式） ---------------- */
typedef struct BenchTime6 {
    double min_ms;
    double med_ms;
    double std_ms;
    int    reps;
    int    batch;
} BenchTime6;

static int cmp_double6(const void *x, const void *y) {
    double a = *(const double *)x, b = *(const double *)y;
    return (a > b) - (a < b);
}

/* use_master=true：每轮 memcpy 母本（母本生成在计时区间外，仅一次）；
 * use_master=false：每轮计时区间外重新生成 B 份互不相同的拷贝（bench.h 语义）。 */
static BenchTime6 bench_time6(SortFunc fn, int n, DistType dist, unsigned seed,
                              int reps, bool use_master, const int *master) {
    BenchTime6 r;
    r.min_ms = r.med_ms = r.std_ms = -1.0;
    r.reps = reps;
    r.batch = 1;

    /* 预热兼估计单次耗时 */
    int *warm = (int *)malloc(sizeof(int) * (size_t)n);
    if (!warm) return r;
    if (use_master) memcpy(warm, master, sizeof(int) * (size_t)n);
    else            generateArray(warm, n, dist, seed ^ 0x9E3779B9u);
    double t0 = now_ms();
    fn(warm, n, 1, NULL);
    double t1 = now_ms();
    free(warm);
    double est = t1 - t0;
    if (!(est > 1e-9)) est = 1e-9;

    /* 批量：目标整批 ~8ms，工作集 ≤ 8MB；母本模式固定 B=1（此时 est 已达
     * 数十 ms 以上，QPC ~100ns 分辨率下单次计时即有 3+ 位有效数字） */
    long long B = 1;
    if (!use_master) {
        B = (long long)(8.0 / est) + 1;
        if (B > 4096) B = 4096;
        while (B > 1 && (double)B * 4.0 * (double)n > 8.0 * 1024.0 * 1024.0) B--;
        r.batch = (int)B;
    }

    double *per = (double *)malloc(sizeof(double) * (size_t)reps);
    if (!per) return r;
    int *buf = (int *)malloc(sizeof(int) * (size_t)n * (size_t)(B > 0 ? B : 1));
    if (!buf) { free(per); return r; }

    for (int rep = 0; rep < reps; rep++) {
        if (use_master) {
            memcpy(buf, master, sizeof(int) * (size_t)n);
            double ta = now_ms();
            fn(buf, n, 1, NULL);
            double tb = now_ms();
            per[rep] = tb - ta;
        } else {
            for (long long i = 0; i < B; i++)
                generateArray(buf + (size_t)i * n, n, dist,
                              seed + 7919u * (unsigned)(rep * (int)B + (int)i + 1));
            double ta = now_ms();
            for (long long i = 0; i < B; i++)
                fn(buf + (size_t)i * n, n, 1, NULL);
            double tb = now_ms();
            per[rep] = (tb - ta) / (double)B;
        }
    }
    free(buf);

    double sum = 0.0, sq = 0.0;
    for (int i = 0; i < reps; i++) { sum += per[i]; sq += per[i] * per[i]; }
    double mean = sum / reps;
    double var = sq / reps - mean * mean;
    if (var < 0.0) var = 0.0;

    double *sorted_per = (double *)malloc(sizeof(double) * (size_t)reps);
    if (sorted_per) {
        memcpy(sorted_per, per, sizeof(double) * (size_t)reps);
        qsort(sorted_per, (size_t)reps, sizeof(double), cmp_double6);
        r.min_ms = sorted_per[0];
        r.med_ms = sorted_per[reps / 2];
        r.std_ms = sqrt(var);
        free(sorted_per);
    }
    free(per);
    return r;
}

/* 计数模式：与 bench.h bench_count 完全同口径（一次运行，独立种子） */
static int bench_count6(SortFunc fn, int n, DistType dist, unsigned seed,
                        SortStats *st, double *tcnt_ms) {
    int ok = 0;
    int *a = (int *)malloc(sizeof(int) * (size_t)n);
    if (!a) return 0;
    generateArray(a, n, dist, seed ^ 0x5DEECE66Du);
    sortcore_rng_reseed(seed ^ 0x2545F491u);
    stats_init(st, 1, "");
    double t0 = now_ms();
    fn(a, n, 1, st);
    double t1 = now_ms();
    ok = checkSorted(a, n, 1);
    free(a);
    if (tcnt_ms) *tcnt_ms = t1 - t0;
    return ok;
}

/* ---------------- 主矩阵 ---------------- */
static const int SCALES[] = { 10000, 100000, 1000000, 10000000, 32000000 };
static const DistType DISTS6[] = {
    DIST_RANDOM_UNIFORM, DIST_GAUSSIAN, DIST_POISSON,
    DIST_SORTED_ASC, DIST_SORTED_DESC
};

int main(int argc, char **argv) {
#ifdef _WIN32
    SetConsoleOutputCP(65001);
#endif
    bool smoke = (argc > 1 && strcmp(argv[1], "smoke") == 0);

#ifdef _WIN32
    g_log = fopen("results\\results_req6.txt", smoke ? "wb" : "wb");
#else
    g_log = fopen("results/results_req6.txt", "wb");
#endif
    if (!g_log) g_log = fopen("results_req6.txt", "wb");

    bothLine("==================================================================");
    bothLine(" 实验内容 6：不同数据量下的时间性能评估（大规模基准）");
    bothLine(" 5 分布（均匀/高斯/泊松/正序/逆序）× 5 规模（1e4~3.2e7）");
    bothLine(" 8 种自实现算法 + AdaptSort + std::sort 基线；升序输出");
    bothLine(" 种子固定可复现；10^4/10^5/10^6 计数与 results_c.csv 交叉一致");
    bothLine("==================================================================");

    /* AdaptSort 一次性成本模型标定（不进入任何计时区间） */
    bothLine("[预热] AdaptSort 成本模型本机标定 ...");
    {
        const int warm_n = 1000000;
        int *w = (int *)malloc(sizeof(int) * (size_t)warm_n);
        if (w) {
            generateArray(w, warm_n, DIST_RANDOM_UNIFORM, 42u);
            adapt_sort(w, warm_n);
            free(w);
        }
    }

    FILE *csv = fopen("results\\results_req6.csv", "wb");
    if (!csv) csv = fopen("results_req6.csv", "wb");
    if (csv)
        fprintf(csv, "lang,dist,algo,n,reps,batch,tmin_ms,tmed_ms,tstd_ms,tcnt_ms,cmp,mv,aux_peak,ok,strategy\n");

    long long t_start = (long long)time(NULL);
    int cells_done = 0, cells_total = 0;
    for (unsigned di = 0; di < sizeof(DISTS6) / sizeof(DISTS6[0]); di++) {
        DistType dist = DISTS6[di];
        for (size_t si = 0; si < sizeof(SCALES) / sizeof(SCALES[0]); si++) {
            int n = SCALES[si];
            if (smoke && n > 10000) continue;
            for (size_t ai = 0; ai < NALGO; ai++) {
                const Algo &A = ALGOS[ai];
                /* Θ(n²) 裁剪：>10^5 只实跑正序档的插入/冒泡（O(n) 特例） */
                if (A.quadratic) {
                    if (n <= 100000) { if (smoke && n > 10000) continue; }
                    else if (!(dist == DIST_SORTED_ASC && strcmp(A.id, "selection") != 0))
                        continue;
                }
                cells_total++;
            }
        }
    }

    for (unsigned di = 0; di < sizeof(DISTS6) / sizeof(DISTS6[0]); di++) {
        DistType dist = DISTS6[di];
        for (size_t si = 0; si < sizeof(SCALES) / sizeof(SCALES[0]); si++) {
            int n = SCALES[si];
            if (smoke && n > 10000) continue;
            bool use_master = (n >= 3000000);
            unsigned seed = 1000003u * (unsigned)n + 97u * (unsigned)(dist + 1);

            int *master = NULL;
            if (use_master) {
                master = (int *)malloc(sizeof(int) * (size_t)n);
                if (master) generateArray(master, n, dist, seed ^ 0x9E3779B9u);
                else use_master = false;
            }

            bothFmt("\n---- %s  n=%d%s ----\n", distName(dist), n,
                    use_master ? "  [母本memcpy计时]" : "");
            bothFmt("  %-10s %12s %12s %10s %16s %16s %12s\n",
                    "算法", "时间min/ms", "时间med/ms", "标准差", "比较次数", "移动次数", "峰值附加B");

            for (size_t ai = 0; ai < NALGO; ai++) {
                const Algo &A = ALGOS[ai];
                bool skip = false;
                if (A.quadratic) {
                    if (n <= 100000) skip = false;
                    else skip = !(dist == DIST_SORTED_ASC && strcmp(A.id, "selection") != 0);
                }
                if (skip) continue;

                int reps = (A.quadratic || n >= 10000000) ? 3 : 5;
                if (smoke) reps = 2;

                g_last_strat[0] = '\0';
                BenchTime6 bt = bench_time6(A.fn, n, dist, seed, reps,
                                            use_master, master);
                SortStats st;
                double tcnt = -1.0;
                int ok = bench_count6(A.fn, n, dist, seed, &st, &tcnt);

                if (csv)
                    fprintf(csv, "req6,%s,%s,%d,%d,%d,%.4f,%.4f,%.4f,%.4f,%lld,%lld,%lld,%d,%s\n",
                            distId(dist), A.id, n, bt.reps, bt.batch,
                            bt.min_ms, bt.med_ms, bt.std_ms, tcnt,
                            st.compares, st.moves, st.aux_peak, ok,
                            A.is_adapt ? g_last_strat : "");
                bothFmt("  %-10s %12.4f %12.4f %10.4f %16lld %16lld %12lld %s\n",
                        A.name, bt.min_ms, bt.med_ms, bt.std_ms,
                        st.compares, st.moves, st.aux_peak, ok ? "" : "[FAIL]");
                if (A.is_adapt && g_last_strat[0])
                    bothFmt("  %-10s 策略: %s\n", "", g_last_strat);
                if (!ok) bothFmt("  ^^^ 排序结果错误！\n");

                cells_done++;
                long long el = (long long)time(NULL) - t_start;
                double rate = el / (double)cells_done;
                bothFmt("  [进度 %d/%d 单元 | 已用 %lld min %02lld s | 预计还需 %.0f min]\n",
                        cells_done, cells_total, el / 60, el % 60,
                        rate * (cells_total - cells_done) / 60.0);
            }
            if (master) free(master);
        }
    }

    bothLine("");
    bothFmt("全部完成：共 %d 单元。输出 results/results_req6.csv / results_req6.txt\n",
            cells_done);
    if (csv) fclose(csv);
    if (g_log) fclose(g_log);
    return 0;
}
