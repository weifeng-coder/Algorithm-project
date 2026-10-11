/* bench.h —— 统一基准测量框架（实验内容 5 完善版 + 实验内容 6 驱动）
 *
 * 双语核心：C 驱动与 C++ 驱动共用同一份测量代码，保证 C vs C++ 对比
 * 在方法论上完全对齐（同一批量规则、同一重复次数、同一统计口径）。
 *
 * 测量方法学（继承 req7 §8 的六个坑的结论）：
 *   1) 等时长批量：先预热一次估计单次耗时，再取批量 B 使"整批一次计时"
 *      约 8 ms —— n=100 时一次排序仅 ~0.5μs，逐次计时会被时钟量化与
 *      调度抖动淹没；批量计时再除以 B 恢复 3 位有效数字。
 *   2) 拷贝工作集上限 8 MB：防止快算法因批量过大获得不同的 cache 状态。
 *   3) 每轮重复前在计时区间外重新生成 B 份互不相同的拷贝 —— 复用已排序
 *      数组曾让插入排序被测快 400 倍（req7 §8.2）。
 *   4) 重复 REPS=5 轮取最小值（机器负载是单侧噪声）为主口径，
 *      同时报告中位数与标准差备查。
 *   5) 计数模式与计时模式各跑一遍：计时时 st=NULL，计数自增不进入测量；
 *      计数模式下另行记录比较/移动/峰值附加空间，并顺带校验有序性。
 */
#ifndef BENCH_H
#define BENCH_H

#include <stdlib.h>
#include <math.h>
#include <string.h>
#include "sortstats.h"
#include "datagen.h"
#include "sortcore.h"     /* sortcore_rng_reseed：计数运行前重置快排内部随机源 */

typedef void (*SortFunc)(int *a, int n, int order, SortStats *st);

typedef struct BenchTime {
    double min_ms;
    double med_ms;
    double std_ms;
    int    reps;
    int    batch;
} BenchTime;

#define BENCH_REPS        5
#define BENCH_TARGET_MS   8.0
#define BENCH_WORKSET     (8.0 * 1024.0 * 1024.0)   /* 拷贝工作集上限 8 MB */
#define BENCH_MAX_BATCH   4096

static int cmp_double(const void *x, const void *y) {
    double a = *(const double *)x, b = *(const double *)y;
    return (a > b) - (a < b);
}

static BenchTime bench_time(SortFunc fn, int n, int order,
                            DistType dist, unsigned seed) {
    BenchTime r;
    r.min_ms = r.med_ms = r.std_ms = -1.0;
    r.reps = BENCH_REPS;
    r.batch = 1;

    /* ---- 预热兼估计单次耗时（同时触发 AdaptSort 等一次性初始化） ---- */
    int *warm = (int *)malloc(sizeof(int) * (size_t)(n > 0 ? n : 1));
    if (!warm) return r;
    generateArray(warm, n, dist, seed ^ 0x9E3779B9u);
    double t0 = now_ms();
    fn(warm, n, order, NULL);
    double t1 = now_ms();
    free(warm);
    double est = t1 - t0;
    if (!(est > 1e-9)) est = 1e-9;

    /* ---- 批量：目标整批 ~8 ms，工作集 ≤ 8 MB ---- */
    long long B = (long long)(BENCH_TARGET_MS / est) + 1;
    if (B > BENCH_MAX_BATCH) B = BENCH_MAX_BATCH;
    while (B > 1 && (double)B * 4.0 * (double)n > BENCH_WORKSET) B--;
    r.batch = (int)B;

    int *buf = (int *)malloc(sizeof(int) * (size_t)n * (size_t)B);
    double per[BENCH_REPS];
    if (!buf) {   /* 批量分配失败则退化为单份 */
        B = 1; r.batch = 1;
        buf = (int *)malloc(sizeof(int) * (size_t)(n > 0 ? n : 1));
        if (!buf) return r;
    }
    for (int rep = 0; rep < BENCH_REPS; rep++) {
        for (long long i = 0; i < B; i++) {   /* 计时区间外重新生成拷贝 */
            generateArray(buf + (size_t)i * n, n, dist,
                          seed + 7919u * (unsigned)(rep * (int)B + (int)i + 1));
        }
        double ta = now_ms();
        for (long long i = 0; i < B; i++)
            fn(buf + (size_t)i * n, n, order, NULL);
        double tb = now_ms();
        per[rep] = (tb - ta) / (double)B;
    }
    free(buf);

    double sum = 0.0, sq = 0.0;
    for (int i = 0; i < BENCH_REPS; i++) { sum += per[i]; sq += per[i] * per[i]; }
    double mean = sum / BENCH_REPS;
    double var = sq / BENCH_REPS - mean * mean;
    if (var < 0.0) var = 0.0;

    double sorted_per[BENCH_REPS];
    memcpy(sorted_per, per, sizeof(per));
    qsort(sorted_per, BENCH_REPS, sizeof(double), cmp_double);

    r.min_ms = sorted_per[0];
    r.med_ms = sorted_per[BENCH_REPS / 2];
    r.std_ms = sqrt(var);
    return r;
}

/* 计数模式：一次运行，记录比较/移动/峰值附加空间，并校验有序性。
 * 返回 1=有序正确。tcnt_ms 输出计数模式的参考耗时（用于证明计数开销可忽略）。 */
static int bench_count(SortFunc fn, int n, int order, DistType dist,
                       unsigned seed, const char *name, SortStats *st,
                       double *tcnt_ms) {
    int ok = 0;
    int *a = (int *)malloc(sizeof(int) * (size_t)(n > 0 ? n : 1));
    if (!a) return 0;
    generateArray(a, n, dist, seed ^ 0x5DEECE66Du);
    /* 使计数成为 (数据, 种子) 的纯函数：与批量 B、此前计时历史无关 */
    sortcore_rng_reseed(seed ^ 0x2545F491u);
    stats_init(st, order, name);
    double t0 = now_ms();
    fn(a, n, order, st);
    double t1 = now_ms();
    ok = checkSorted(a, n, order);
    free(a);
    if (tcnt_ms) *tcnt_ms = t1 - t0;
    return ok;
}

#endif /* BENCH_H */
