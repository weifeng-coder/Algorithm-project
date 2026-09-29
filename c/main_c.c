/* main_c.c —— C 构建驱动（统一版）
 *
 * 职责：
 *   1) 正确性测试：8 种算法 × 6 分布 × 升/降序 × 8 个规模（含 n=0/1/2 边界），
 *      与独立参照实现（C 标准库 qsort）逐元素比对；
 *   2) 计数模块自检（TC-4）：已知理论值对照 —— 插入排序正序输入 = n-1 次比较、
 *      2(n-1) 次移动；冒泡正序 = n-1 次比较、0 次移动；选择任意输入 =
 *      n(n-1)/2 次比较；基数排序比较数恒为 0；
 *   3) 全矩阵基准（实验内容 6）：6 分布 × 5 规模 × (8 自实现算法 + qsort 基线)。
 *      计时模式与计数模式各跑一遍（bench.h）；Θ(n²) 三算法在 n>10⁴ 档不实跑。
 *
 * 输出：results/results_c.txt（人读报告）+ results/results_c.csv（机器可读，
 *       供 tools/compare_c_cpp.py 做 C vs C++ 对比）。
 *
 * 编译：gcc -O2 -std=c11 -Wall -I common c/main_c.c -o build/sort_demo_c.exe
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#ifdef _WIN32
#include <windows.h>
#endif
#include "sortstats.h"
#include "datagen.h"
#include "sortcore.h"
#include "bench.h"

/* ---------------- 算法登记表 ---------------- */
typedef struct Algo {
    const char *id;    /* CSV 用 ASCII id */
    const char *name;  /* 中文显示名 */
    SortFunc fn;
    int quadratic;     /* Θ(n²) 族：n > QUAD_CAP 不实跑 */
} Algo;

#define QUAD_CAP 10000

static const Algo ALGOS[] = {
    { "insertion", "直接插入", insertionSort, 1 },
    { "bubble",    "冒泡",     bubbleSort,    1 },
    { "selection", "选择",     selectSort,    1 },
    { "shell",     "希尔",     shellSort,     0 },
    { "quick",     "快速",     quickSort,     0 },
    { "heap",      "堆",       heapSort,      0 },
    { "radix",     "基数",     radixSort,     0 },
    { "merge",     "归并",     mergeSort,     0 },
};
#define NALGO (sizeof(ALGOS) / sizeof(ALGOS[0]))

/* ---- C 标准库 qsort 基线（比较器经函数指针调用，无法省略 —— 这正是
 *      C 与 C++ std::sort 对比的关键差异点，见 c_vs_cpp.md） ---- */
static SortStats *g_qbridge = NULL;
static int g_qorder = 1;
static long long g_qcmp = 0;

static int qcmp_int(const void *x, const void *y) {
    if (g_qbridge) g_qcmp++;
    int a = *(const int *)x, b = *(const int *)y;
    if (g_qorder == 1) return (a > b) - (a < b);
    return (b > a) - (b < a);
}

static void qsortWrap(int *a, int n, int order, SortStats *st) {
    g_qcmp = 0;
    g_qorder = order;
    g_qbridge = st;
    qsort(a, (size_t)n, sizeof(int), qcmp_int);
    g_qbridge = NULL;
    if (st) {
        stats_init(st, order, "qsort");
        st->compares = g_qcmp;
        st->moves = -1;      /* 库实现不暴露移动计数 */
        st->aux_peak = -1;
    }
}

/* ---------------- 输出：同时写屏幕与 txt ---------------- */
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

/* ---------------- 独立参照排序（qsort + 反转） ---------------- */
static void refSort(int *a, int n, int order) {
    qsort(a, (size_t)n, sizeof(int), qcmp_int);  /* qcmp_int 在 g_qorder=1 下为升序 */
    if (order == -1) {
        for (int i = 0, j = n - 1; i < j; i++, j--) {
            int t = a[i]; a[i] = a[j]; a[j] = t;
        }
    }
}

/* ---------------- 1) 正确性测试（数据经顺序表 SeqList 存放，实验内容 1 的 ADT 实际接入） ---------------- */
static int correctnessTest(void) {
    const int ns[] = { 0, 1, 2, 3, 5, 17, 100, 1000 };
    int fails = 0, total = 0;
    bothLine("============ 正确性测试（与 qsort 参照逐元素比对） ============");
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
                    ALGOS[ai].fn(L->data, n, order, NULL);
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
        bothFmt("  %-8s : %s\n", ALGOS[ai].name, algo_fails ? "FAIL" : "OK (96 组)");
    }
    SeqList_Free(L);
    bothFmt("  合计: %d 组, 失败 %d 组\n", total, fails);
    return fails;
}

/* ---------------- 2) 计数模块自检（TC-4） ---------------- */
static int countingSelfTest(void) {
    const int n = 1000;
    int fails = 0;
    SortStats st;
    int *a = (int *)malloc(sizeof(int) * (size_t)n);
    bothLine("------------ 计数模块自检（与理论值对照） ------------");

    /* 插入排序：正序输入 → n-1 次比较、2(n-1) 次移动 */
    for (int i = 0; i < n; i++) a[i] = i + 1;
    insertionSort(a, n, 1, &st);
    bothFmt("  插入排序/正序: cmp=%lld (期望 %d) mv=%lld (期望 %d) %s\n",
            st.compares, n - 1, st.moves, 2 * (n - 1),
            (st.compares == n - 1 && st.moves == 2 * (n - 1)) ? "OK" : "FAIL");
    if (st.compares != n - 1 || st.moves != 2 * (n - 1)) fails++;

    /* 冒泡排序：正序输入 → 1 趟即止：n-1 次比较、0 次移动 */
    for (int i = 0; i < n; i++) a[i] = i + 1;
    bubbleSort(a, n, 1, &st);
    bothFmt("  冒泡排序/正序: cmp=%lld (期望 %d) mv=%lld (期望 0) %s\n",
            st.compares, n - 1, st.moves,
            (st.compares == n - 1 && st.moves == 0) ? "OK" : "FAIL");
    if (st.compares != n - 1 || st.moves != 0) fails++;

    /* 选择排序：任意输入 → 比较恒为 n(n-1)/2 */
    generateArray(a, n, DIST_RANDOM_UNIFORM, 777u);
    selectSort(a, n, 1, &st);
    long long expect = (long long)n * (n - 1) / 2;
    bothFmt("  选择排序/均匀: cmp=%lld (期望 %lld) %s\n",
            st.compares, expect, (st.compares == expect) ? "OK" : "FAIL");
    if (st.compares != expect) fails++;

    /* 基数排序：不做关键字比较 → cmp 恒为 0 */
    generateArray(a, n, DIST_RANDOM_UNIFORM, 778u);
    radixSort(a, n, 1, &st);
    bothFmt("  基数排序/均匀: cmp=%lld (期望 0) aux=%lld (期望 %d) %s\n",
            st.compares, st.aux_peak, (int)(n * sizeof(int)),
            (st.compares == 0 && st.aux_peak == (long long)n * sizeof(int)) ? "OK" : "FAIL");
    if (st.compares != 0 || st.aux_peak != (long long)n * (long long)sizeof(int)) fails++;

    /* 归并排序：附加空间 = O(n) */
    generateArray(a, n, DIST_RANDOM_UNIFORM, 779u);
    mergeSort(a, n, 1, &st);
    bothFmt("  归并排序/均匀: aux=%lld (期望 %d) %s\n",
            st.aux_peak, (int)(n * sizeof(int)),
            (st.aux_peak == (long long)n * (long long)sizeof(int)) ? "OK" : "FAIL");
    if (st.aux_peak != (long long)n * (long long)sizeof(int)) fails++;

    /* 快速排序：全等输入 → 三路划分一趟完成，比较次数 O(n) */
    for (int i = 0; i < n; i++) a[i] = 42;
    quickSort(a, n, 1, &st);
    bothFmt("  快速排序/全等: cmp=%lld (期望 <2n=%d，二路退化会达 O(n²)) %s\n",
            st.compares, 2 * n, (st.compares < 2 * n) ? "OK" : "FAIL");
    if (st.compares >= 2 * n) fails++;

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
            for (size_t ai = 0; ai < NALGO + 1; ai++) {
                SortFunc fn;
                const char *id, *name;
                int quadratic;
                if (ai < NALGO) {
                    fn = ALGOS[ai].fn; id = ALGOS[ai].id;
                    name = ALGOS[ai].name; quadratic = ALGOS[ai].quadratic;
                } else {
                    fn = qsortWrap; id = "qsort"; name = "qsort基线"; quadratic = 0;
                }
                if (quadratic && n > QUAD_CAP) {
                    fprintf(csv, "c,%s,%s,%d,%d,%d,-1,-1,-1,-1,-1,-1,-1,1,\n",
                            distId(dist), id, n, BENCH_REPS, 1);
                    bothFmt("  %-10s %12s %12s %10s %16s %16s %12s\n",
                            name, "—", "—", "—", "—", "—", "—");
                    continue;
                }
                unsigned seed = 1000003u * (unsigned)n + 97u * (d + 1u);
                BenchTime bt = bench_time(fn, n, 1, dist, seed);
                int ok = bench_count(fn, n, 1, dist, seed, name, &st, &tcnt);
                fprintf(csv, "c,%s,%s,%d,%d,%d,%.4f,%.4f,%.4f,%.4f,%lld,%lld,%lld,%d,\n",
                        distId(dist), id, n, bt.reps, bt.batch,
                        bt.min_ms, bt.med_ms, bt.std_ms, tcnt,
                        st.compares, st.moves, st.aux_peak, ok);
                bothFmt("  %-10s %12.4f %12.4f %10.4f %16lld %16lld %12lld %s\n",
                        name, bt.min_ms, bt.med_ms, bt.std_ms,
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
    g_log = fopen("results\\results_c.txt", "wb");
#else
    g_log = fopen("results/results_c.txt", "wb");
#endif
    if (!g_log) g_log = fopen("results_c.txt", "wb");

    bothLine("==============================================================");
    bothLine(" 排序算法性能比较与应用 —— 统一版 C 构建 (gcc -O2 -std=c11)");
    bothLine(" 8 种自实现算法 + qsort 库基线；6 分布 × 5 规模；种子固定可复现");
    bothLine("==============================================================");

    int f1 = correctnessTest();
    bothLine("");
    int f2 = countingSelfTest();
    bothLine("");
    FILE *csv = fopen("results\\results_c.csv", "wb");
    if (!csv) csv = fopen("results_c.csv", "wb");
    benchMatrix(csv);
    if (csv) fclose(csv);

    bothLine("");
    bothFmt("全部完成：正确性失败 %d，计数自检失败 %d（详见 results/results_c.txt / results_c.csv）\n",
            f1, f2);
    if (g_log) fclose(g_log);
    return (f1 || f2) ? 1 : 0;
}
