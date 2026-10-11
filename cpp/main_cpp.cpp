/* main_cpp.cpp —— C++ 构建驱动（统一版）
 *
 * 与 c/main_c.c 平行的 C++ 侧驱动。核心设计：
 *   * 8 种自实现算法使用与 C 构建**完全相同**的源码（common/sortcore.h，
 *     此处以 C++ 编译），因此 6 分布 × 5 规模矩阵上两构建的比较/移动计数
 *     必须完全一致（compare_c_cpp.py 断言），时间差异即语言实现差异；
 *   * 额外挂载第七点的 AdaptSort（cpp/adaptsort/，req7 v3 原样引入）：
 *     通过 sb::Counters / sb::mem 桥接到统一 SortStats —— 计时模式下
 *     counters().on=false（默认），计数开销不进入时间测量；计数模式下
 *     读取 cmp/mv/峰值附加空间，并记录决策策略（strategy 列）；
 *   * 额外挂载 std::sort 基线：比较器为内联 lambda（C++ 与 C qsort 的
 *     函数指针比较器形成语言设施层面的对照）。移动数不适用，记 -1。
 *
 * 输出：results/results_cpp.txt + results/results_cpp.csv
 *
 * 编译：g++ -O2 -std=c++17 -Wall -I common -I cpp/adaptsort cpp/main_cpp.cpp
 *       -o build/sort_demo_cpp.exe
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <algorithm>
#include <string>
#ifdef _WIN32
#include <windows.h>
#endif
#include "sortstats.h"
#include "datagen.h"
#include "sortcore.h"
#include "bench.h"

#include "adaptsort.h"    /* sb::adapt_sort / strat_name / last_strategy */
#include "counters.h"     /* sb::counters / sb::mem / sb::CountScope */

using namespace sb;

/* ---------------- 算法登记表 ---------------- */
typedef void (*SortFunc)(int *a, int n, int order, SortStats *st);

typedef struct Algo {
    const char *id;
    const char *name;
    SortFunc fn;
    int quadratic;
    bool is_adapt;     /* 记录决策策略列 */
} Algo;

#define QUAD_CAP 10000

/* ---- AdaptSort 桥接：统一 SortStats ← sb::Counters/sb::mem ---- */
static char g_last_strat[64] = "";

static void adaptWrap(int *a, int n, int order, SortStats *st) {
    if (st) {
        counters().reset();
        counters().on = true;
        mem().reset();
    }
    adapt_sort(a, n);              /* 升序主体（全部自适应分支） */
    if (order == -1) {             /* 降序 = 升序 + O(n) 反转（AdaptSort 文档口径）；
                                    * reverse_array 内部经 swp 计数，计数模式下计入 mv */
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

/* ---- std::sort 基线：内联比较器；计数仅在计数模式开启 ---- */
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
    if (st) { st->moves = -1; st->aux_peak = -1; }  /* 库实现不暴露 */
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
}

/* ---------------- 独立参照排序（std::sort + 反转） ---------------- */
static void refSort(int *a, int n, int order) {
    std::sort(a, a + n);
    if (order == -1)
        for (int i = 0, j = n - 1; i < j; i++, j--)
            std::swap(a[i], a[j]);
}

/* ---------------- 1) 正确性测试（数据经顺序表 SeqList 存放，实验内容 1 的 ADT 实际接入） ---------------- */
static int correctnessTest(void) {
    const int ns[] = { 0, 1, 2, 3, 5, 17, 100, 1000 };
    int fails = 0, total = 0;
    bothLine("============ 正确性测试（与 std::sort 参照逐元素比对） ============");
    SeqList *L = SeqList_Create(16);      /* 实验内容 1：待排序序列存放于顺序表 */
    for (size_t ai = 0; ai < NALGO; ai++) {
        int algo_fails = 0;
        for (unsigned d = 0; d < DIST_COUNT; d++) {
            for (int order = 1; order >= -1; order -= 2) {
                for (size_t ni = 0; ni < sizeof(ns) / sizeof(ns[0]); ni++) {
                    int n = ns[ni];
                    int *ref = (int *)malloc(sizeof(int) * (size_t)(n > 0 ? n : 1));
                    generateData(L, n, (DistType)d, 12345u + (unsigned)n * 31u + (unsigned)order);
                    memcpy(ref, L->data, sizeof(int) * (size_t)n);
                    refSort(ref, n, order);
                    ALGOS[ai].fn(L->data, n, order, nullptr);
                    total++;
                    if (!checkSorted(L->data, n, order) ||
                        (n > 0 && memcmp(L->data, ref, sizeof(int) * (size_t)n) != 0)) {
                        fails++;
                        algo_fails++;
                        if (algo_fails <= 3)
                            bothFmt("  [FAIL] %s dist=%s order=%d n=%d\n",
                                    ALGOS[ai].name, distName((DistType)d), order, n);
                    }
                    free(ref);
                }
            }
        }
        bothFmt("  %-10s : %s\n", ALGOS[ai].name, algo_fails ? "FAIL" : "OK (96 组)");
    }
    SeqList_Free(L);
    bothFmt("  合计: %d 组, 失败 %d 组\n", total, fails);
    return fails;
}

/* ---------------- 2) 计数模块自检（TC-4，C++ 侧追加 AdaptSort 检查） ---------------- */
static int countingSelfTest(void) {
    const int n = 1000;
    int fails = 0;
    SortStats st;
    int *a = (int *)malloc(sizeof(int) * (size_t)n);
    bothLine("------------ 计数模块自检（与理论值对照） ------------");

    for (int i = 0; i < n; i++) a[i] = i + 1;
    insertionSort(a, n, 1, &st);
    bothFmt("  插入排序/正序: cmp=%lld (期望 %d) mv=%lld (期望 %d) %s\n",
            st.compares, n - 1, st.moves, 2 * (n - 1),
            (st.compares == n - 1 && st.moves == 2 * (n - 1)) ? "OK" : "FAIL");
    if (st.compares != n - 1 || st.moves != 2 * (n - 1)) fails++;

    for (int i = 0; i < n; i++) a[i] = i + 1;
    bubbleSort(a, n, 1, &st);
    bothFmt("  冒泡排序/正序: cmp=%lld (期望 %d) mv=%lld (期望 0) %s\n",
            st.compares, n - 1, st.moves,
            (st.compares == n - 1 && st.moves == 0) ? "OK" : "FAIL");
    if (st.compares != n - 1 || st.moves != 0) fails++;

    generateArray(a, n, DIST_RANDOM_UNIFORM, 777u);
    selectSort(a, n, 1, &st);
    long long expect = (long long)n * (n - 1) / 2;
    bothFmt("  选择排序/均匀: cmp=%lld (期望 %lld) %s\n",
            st.compares, expect, (st.compares == expect) ? "OK" : "FAIL");
    if (st.compares != expect) fails++;

    generateArray(a, n, DIST_RANDOM_UNIFORM, 778u);
    radixSort(a, n, 1, &st);
    bothFmt("  基数排序/均匀: cmp=%lld (期望 0) aux=%lld %s\n",
            st.compares, st.aux_peak,
            (st.compares == 0 && st.aux_peak == (long long)n * (long long)sizeof(int)) ? "OK" : "FAIL");
    if (st.compares != 0 || st.aux_peak != (long long)n * (long long)sizeof(int)) fails++;

    /* AdaptSort：正序输入 → 直通策略，比较 ≈ n-1 + 探测(2(m-1))，移动 0 */
    for (int i = 0; i < n; i++) a[i] = i + 1;
    adaptWrap(a, n, 1, &st);
    bothFmt("  AdaptSort/正序: cmp=%lld mv=%lld 策略=%s %s\n",
            st.compares, st.moves, g_last_strat,
            (st.moves == 0 && st.compares < 2 * n) ? "OK" : "FAIL");
    if (!(st.moves == 0 && st.compares < 2 * n)) fails++;

    free(a);
    return fails;
}

/* ---------------- 3) 全矩阵基准 ---------------- */
static const int SCALES[] = { 100, 1000, 10000, 100000, 1000000 };

static void benchMatrix(FILE *csv) {
    SortStats st;
    double tcnt = -1.0;
    bothLine("============ 全矩阵基准（6 分布 × 5 规模） ============");
    bothLine("说明: Θ(n²) 三算法在 n>10^4 档不实跑(记 -1)；时间取 5 轮最小值");
    fprintf(csv, "lang,dist,algo,n,reps,batch,tmin_ms,tmed_ms,tstd_ms,tcnt_ms,cmp,mv,aux_peak,ok,strategy\n");

    for (unsigned d = 0; d < DIST_COUNT; d++) {
        DistType dist = (DistType)d;
        for (size_t si = 0; si < sizeof(SCALES) / sizeof(SCALES[0]); si++) {
            int n = SCALES[si];
            bothFmt("\n---- %s  n=%d ----\n", distName(dist), n);
            bothFmt("  %-10s %12s %12s %10s %16s %16s %12s\n",
                    "算法", "时间min/ms", "时间med/ms", "标准差", "比较次数", "移动次数", "峰值附加B");
            for (size_t ai = 0; ai < NALGO; ai++) {
                const Algo &A = ALGOS[ai];
                if (A.quadratic && n > QUAD_CAP) {
                    fprintf(csv, "cpp,%s,%s,%d,%d,%d,-1,-1,-1,-1,-1,-1,-1,1,\n",
                            distId(dist), A.id, n, BENCH_REPS, 1);
                    bothFmt("  %-10s %12s %12s %10s %16s %16s %12s\n",
                            A.name, "—", "—", "—", "—", "—", "—");
                    continue;
                }
                unsigned seed = 1000003u * (unsigned)n + 97u * (d + 1u);
                g_last_strat[0] = '\0';
                BenchTime bt = bench_time(A.fn, n, 1, dist, seed);
                int ok = bench_count(A.fn, n, 1, dist, seed, A.name, &st, &tcnt);
                fprintf(csv, "cpp,%s,%s,%d,%d,%d,%.4f,%.4f,%.4f,%.4f,%lld,%lld,%lld,%d,%s\n",
                        distId(dist), A.id, n, bt.reps, bt.batch,
                        bt.min_ms, bt.med_ms, bt.std_ms, tcnt,
                        st.compares, st.moves, st.aux_peak, ok, g_last_strat);
                bothFmt("  %-10s %12.4f %12.4f %10.4f %16lld %16lld %12lld %s\n",
                        A.name, bt.min_ms, bt.med_ms, bt.std_ms,
                        st.compares, st.moves, st.aux_peak, ok ? "" : "[FAIL]");
                if (!ok) bothFmt("  ^^^ 排序结果错误！\n");
            }
        }
    }
}

int main(void) {
#ifdef _WIN32
    SetConsoleOutputCP(65001);
#endif
#ifdef _WIN32
    g_log = fopen("results\\results_cpp.txt", "wb");
#else
    g_log = fopen("results/results_cpp.txt", "wb");
#endif
    if (!g_log) g_log = fopen("results_cpp.txt", "wb");

    bothLine("==============================================================");
    bothLine(" 排序算法性能比较与应用 —— 统一版 C++ 构建 (g++ -O2 -std=c++17)");
    bothLine(" 8 种自实现算法(与 C 构建同源码) + AdaptSort + std::sort 基线");
    bothLine(" 6 分布 × 5 规模；种子固定可复现，与 C 构建数据逐字节相同");
    bothLine("==============================================================");

    /* AdaptSort 一次性成本模型标定（~0.5s，不进入任何计时区间） */
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

    int f1 = correctnessTest();
    bothLine("");
    int f2 = countingSelfTest();
    bothLine("");
    FILE *csv = fopen("results\\results_cpp.csv", "wb");
    if (!csv) csv = fopen("results_cpp.csv", "wb");
    benchMatrix(csv);
    if (csv) fclose(csv);

    bothLine("");
    bothFmt("全部完成：正确性失败 %d，计数自检失败 %d（详见 results/results_cpp.txt / results_cpp.csv）\n",
            f1, f2);
    if (g_log) fclose(g_log);
    return (f1 || f2) ? 1 : 0;
}
