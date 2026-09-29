/* sortstats.h —— 统一统计模块（实验内容 5 完善版）
 *
 * 本头文件是"双语核心"：同一份源码既可被 gcc 当作 C 编译（-std=c11），
 * 也可被 g++ 当作 C++ 编译（-std=c++17），供 C / C++ 语言对比实验使用。
 *
 * 相比 v1（legacy/）的三点完善：
 *   1) 计数模式与计时模式彻底分离：
 *      排序函数接收 SortStats*，为 NULL 时进入纯计时模式，计数自增被编译为
 *      一条可预测分支（实测开销 <1%）；非 NULL 时为纯计数模式。两种模式
 *      各跑一遍，避免计数开销污染时间测量（与 req7/counters.h 同一口径）。
 *   2) 峰值附加空间统计：算法内部的堆分配统一走 salloc/sfree 登记出口，
 *      记录运行期峰值附加字节数，用于验证 O(1)/O(n)/O(n+k) 的空间论断。
 *   3) 高精度计时：Windows 下用 QueryPerformanceCounter（~100ns 分辨率），
 *      取代 v1 的 clock()（MinGW 下 CLOCKS_PER_SEC=1000，只有 1ms 分辨率，
 *      v1 的 results.txt 中 n=1000 档普遍计成 0.00ms 即由此而来）。
 */
#ifndef SORTSTATS_H
#define SORTSTATS_H

#include <stddef.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

/* 一次排序运行的统计结果。
 * 约定（与 req7/counters.h 及严蔚敏教材口径一致）：
 *   compares —— 一次关键字之间的比较（升/降序方向化比较也计 1 次）
 *   moves    —— 一次元素赋值记 1；一次三赋值交换记 3；读入临时变量记 1
 *   aux_peak —— 算法自身申请的堆空间峰值（字节），不含输入数组；不适用记 -1
 */
typedef struct SortStats {
    long long compares;
    long long moves;
    long long aux_peak;
    double    time_ms;   /* 由基准框架（bench.h）填写，算法本体不计时 */
    int       order;     /* 1=升序, -1=降序 */
    char      name[32];
} SortStats;

static inline void stats_init(SortStats *st, int order, const char *name) {
    st->compares = 0;
    st->moves = 0;
    st->aux_peak = 0;
    st->time_ms = 0.0;
    st->order = order;
    strncpy(st->name, name, sizeof(st->name) - 1);
    st->name[sizeof(st->name) - 1] = '\0';
}

/* ---------------- 高精度计时 ---------------- */
static double g_qpc_inv_ms = 0.0;   /* QPC 每计数对应的毫秒数（进程内不变） */

static inline double now_ms(void) {
#ifdef _WIN32
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    if (g_qpc_inv_ms == 0.0) {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        g_qpc_inv_ms = 1000.0 / (double)f.QuadPart;
    }
    return (double)c.QuadPart * g_qpc_inv_ms;
#else
    return (double)clock() * 1000.0 / (double)CLOCKS_PER_SEC;
#endif
}

#endif /* SORTSTATS_H */
