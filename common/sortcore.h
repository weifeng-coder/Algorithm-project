/* sortcore.h —— 实验内容 3/4：八种排序算法（统一实现，双语核心）
 *
 * 同一份源码被 C 构建（gcc -std=c11）与 C++ 构建（g++ -std=c++17）共同使用：
 *   * 语言对比实验的前提 —— 两个构建的算法逻辑、比较器、随机源逐语句相同，
 *     因此比较次数/移动次数在两种构建下必须完全一致（compare_c_cpp.py 断言），
 *     时间差异即纯粹的"语言实现差异"。
 *   * 统计挂钩（实验内容 5 完善版）：
 *       - st == NULL  → 纯计时模式，计数自增退化为一条可预测分支；
 *       - st != NULL  → 纯计数模式，记录比较/移动/峰值附加空间。
 *     两种模式在驱动中各跑一遍，计数开销不进入时间测量。
 *   * 堆分配统一走 salloc/sfree（16 字节头部记录块大小），峰值附加空间
 *     登记进 st->aux_peak。算法递归栈不属于堆附加空间，不计入（报告中说明）。
 *
 * 算法要点与 v1（legacy/src/sort.c）一致：
 *   插入/冒泡（带提前终止）/选择：O(n²) 基线；
 *   希尔：Shell 增量 n/2 递减；
 *   快排：xorshift32 随机基准 + 三路划分 + 小区间(≤16)插入 + 尾递归消除；
 *   堆排序：方向化堆（升序大顶堆/降序小顶堆）；
 *   基数：LSD 按字节（基数 256）4 趟，最高字节翻转符号位支持有符号数，
 *         降序 = 升序结果原地反转；不做关键字比较（compares=0）；
 *   归并：递归 + O(n) 辅助数组，稳定。
 */
#ifndef SORTCORE_H
#define SORTCORE_H

#include <stdlib.h>
#include <string.h>
#include "sortstats.h"

/* ---------------- 统一统计挂钩（每个构建单元仅此一份状态） ---------------- */

static SortStats *g_ss = NULL;    /* 当前计数目标；NULL = 计时模式 */
static long long  g_aux_cur = 0;  /* 当前附加堆字节（支持嵌套登记） */

/* 方向化关键字比较：order=1 升序 / -1 降序。
 * 返回 >0 表示 x 应排在 y 之后。用 (x>y)-(x<y) 而非 x-y，避免溢出。 */
static int scmp(int x, int y, int order) {
    if (g_ss) g_ss->compares++;
    if (order == 1) return (x > y) - (x < y);
    else            return (y > x) - (y < x);
}

/* 记 k 次元素移动 */
static void smv(int k) {
    if (g_ss) g_ss->moves += k;
}

/* 受登记的堆分配：块头前 16 字节记录大小，保证任意嵌套可正确注销。
 * 返回 16 字节对齐的数据区指针（malloc 返回值已满足最大对齐，+16 保持）。 */
static void *salloc(size_t bytes) {
    unsigned char *raw = (unsigned char *)malloc(bytes + 16);
    if (!raw) return NULL;
    memcpy(raw, &bytes, sizeof(size_t));
    g_aux_cur += (long long)bytes;
    if (g_ss && g_aux_cur > g_ss->aux_peak) g_ss->aux_peak = g_aux_cur;
    return raw + 16;
}

static void sfree(void *p) {
    if (!p) return;
    unsigned char *raw = (unsigned char *)p - 16;
    size_t bytes;
    memcpy(&bytes, raw, sizeof(size_t));
    g_aux_cur -= (long long)bytes;
    free(raw);
}

/* 快速排序专用的内部随机源（固定初值 → 两种构建下枢轴序列完全一致） */
static unsigned int g_qrng = 2463534242u;
static unsigned int xorshift32(void) {
    unsigned int x = g_qrng;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return g_qrng = x;
}

/* 确定性重置快排内部随机源。
 * 必须：快排的比较/移动次数依赖枢轴序列，而枢轴序列依赖进程内此前
 * 消耗的随机数总量；基准框架的批量 B 由实测预热耗时决定（两构建/两轮
 * 运行不必相同），若不重置，计数模式的 quick 行不可复现。
 * 计数模式每次运行前由 bench_count 调用本函数，使计数成为
 * (数据, 单元种子) 的纯函数。 */
static void sortcore_rng_reseed(unsigned int seed) { g_qrng = seed ? seed : 1u; }

#define SORT_ENTER(st) (g_ss = (st))
#define SORT_EXIT()    (g_ss = NULL)

/* ============================================================
 * 实验内容 3：简单排序
 * ============================================================ */

void insertionSort(int *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "直接插入");
    SORT_ENTER(st);
    for (int i = 1; i < n; i++) {
        int key = a[i]; smv(1);            /* 取出哨兵 */
        int j = i - 1;
        while (j >= 0 && scmp(a[j], key, order) > 0) {
            a[j + 1] = a[j]; smv(1);       /* 元素后移 */
            j--;
        }
        a[j + 1] = key; smv(1);            /* 插入到位 */
    }
    SORT_EXIT();
}

void bubbleSort(int *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "冒泡");
    SORT_ENTER(st);
    for (int i = 0; i < n - 1; i++) {
        int swapped = 0;
        for (int j = 0; j < n - 1 - i; j++) {
            if (scmp(a[j], a[j + 1], order) > 0) {
                int t = a[j]; a[j] = a[j + 1]; a[j + 1] = t;
                smv(3);                    /* 一次交换记 3 次移动 */
                swapped = 1;
            }
        }
        if (!swapped) break;               /* 已有序，提前结束 */
    }
    SORT_EXIT();
}

void selectSort(int *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "选择");
    SORT_ENTER(st);
    for (int i = 0; i < n - 1; i++) {
        int sel = i;
        for (int j = i + 1; j < n; j++) {
            if (scmp(a[j], a[sel], order) < 0) sel = j;
        }
        if (sel != i) {
            int t = a[i]; a[i] = a[sel]; a[sel] = t;
            smv(3);
        }
    }
    SORT_EXIT();
}

/* ============================================================
 * 实验内容 4：高级排序
 * ============================================================ */

void shellSort(int *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "希尔");
    SORT_ENTER(st);
    for (int gap = n / 2; gap > 0; gap /= 2) {
        for (int i = gap; i < n; i++) {
            int key = a[i]; smv(1);
            int j = i - gap;
            while (j >= 0 && scmp(a[j], key, order) > 0) {
                a[j + gap] = a[j]; smv(1);
                j -= gap;
            }
            a[j + gap] = key; smv(1);
        }
    }
    SORT_EXIT();
}

/* ---- 快速排序：随机基准 + 三路划分 + 小区间插入 + 尾递归消除 ---- */
static void insort_range(int *a, int l, int r, int order) {
    for (int i = l + 1; i <= r; i++) {
        int key = a[i]; smv(1);
        int j = i - 1;
        while (j >= l && scmp(a[j], key, order) > 0) {
            a[j + 1] = a[j]; smv(1);
            j--;
        }
        a[j + 1] = key; smv(1);
    }
}

static void quick_rec(int *a, int l, int r, int order) {
    while (r - l > 16) {
        /* 随机选基准换到 a[l]，避免正/逆序数据上划分失衡 */
        int pi = l + (int)(xorshift32() % (unsigned)(r - l + 1));
        { int t = a[pi]; a[pi] = a[l]; a[l] = t; smv(3); }
        int pivot = a[l]; smv(1);

        /* 三路划分（荷兰国旗）：<pivot | =pivot | >pivot */
        int lt = l, gt = r, i = l + 1;
        while (i <= gt) {
            int cmp = scmp(a[i], pivot, order);
            if (cmp < 0) {
                int t = a[lt]; a[lt] = a[i]; a[i] = t; smv(3); lt++; i++;
            } else if (cmp > 0) {
                int t = a[i]; a[i] = a[gt]; a[gt] = t; smv(3); gt--;
            } else {
                i++;
            }
        }
        /* 递归较小一侧，另一侧循环处理 */
        if (lt - l < r - gt) {
            quick_rec(a, l, lt - 1, order);
            l = gt + 1;
        } else {
            quick_rec(a, gt + 1, r, order);
            r = lt - 1;
        }
    }
    insort_range(a, l, r, order);
}

void quickSort(int *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "快速");
    SORT_ENTER(st);
    if (n > 1) quick_rec(a, 0, n - 1, order);
    SORT_EXIT();
}

/* ---- 堆排序 ---- */
static void siftDown(int *a, int start, int end, int order) {
    int i = start, j = 2 * i + 1;
    int tmp = a[i]; smv(1);
    while (j <= end) {
        if (j + 1 <= end && scmp(a[j], a[j + 1], order) < 0) j++;
        if (scmp(tmp, a[j], order) >= 0) break;
        a[i] = a[j]; smv(1);
        i = j; j = 2 * i + 1;
    }
    a[i] = tmp; smv(1);
}

void heapSort(int *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "堆");
    SORT_ENTER(st);
    for (int i = n / 2 - 1; i >= 0; i--)
        siftDown(a, i, n - 1, order);
    for (int i = n - 1; i > 0; i--) {
        int t = a[0]; a[0] = a[i]; a[i] = t; smv(3);
        siftDown(a, 0, i - 1, order);
    }
    SORT_EXIT();
}

/* ---- 基数排序（LSD 按字节，假定小端序；compares 恒为 0） ---- */
void radixSort(int *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "基数");
    SORT_ENTER(st);
    if (n > 1) {
        int *tmp = (int *)salloc(sizeof(int) * (size_t)n);
        if (!tmp) { SORT_EXIT(); return; }
        unsigned char *bytes = (unsigned char *)a;
        for (int byteIdx = 0; byteIdx < 4; byteIdx++) {
            int bucketCount[256];
            memset(bucketCount, 0, sizeof(bucketCount));
            for (int i = 0; i < n; i++) {
                unsigned char b = bytes[(size_t)i * 4 + byteIdx];
                if (byteIdx == 3) b ^= 0x80;   /* 最高字节翻转符号位 */
                bucketCount[b]++;
            }
            for (int k = 1; k < 256; k++) bucketCount[k] += bucketCount[k - 1];
            for (int i = n - 1; i >= 0; i--) {  /* 逆序收集保稳定 */
                unsigned char b = bytes[(size_t)i * 4 + byteIdx];
                if (byteIdx == 3) b ^= 0x80;
                tmp[--bucketCount[b]] = a[i]; smv(1);
            }
            memcpy(a, tmp, sizeof(int) * (size_t)n);
            smv(n);   /* 整块拷回按逐元素赋值计 n 次移动（每轮） */
            bytes = (unsigned char *)a;
        }
        sfree(tmp);
        if (order == -1) {
            for (int i = 0, j = n - 1; i < j; i++, j--) {
                int t = a[i]; a[i] = a[j]; a[j] = t; smv(3);
            }
        }
    }
    SORT_EXIT();
}

/* ---- 归并排序（递归 + O(n) 辅助数组，稳定） ---- */
static void merge_rec(int *a, int *tmp, int l, int r, int order) {
    if (l >= r) return;
    int mid = l + (r - l) / 2;
    merge_rec(a, tmp, l, mid, order);
    merge_rec(a, tmp, mid + 1, r, order);
    int i = l, j = mid + 1, k = l;
    while (i <= mid && j <= r) {
        if (scmp(a[i], a[j], order) <= 0) tmp[k++] = a[i++];
        else                              tmp[k++] = a[j++];
        smv(1);
    }
    while (i <= mid) { tmp[k++] = a[i++]; smv(1); }
    while (j <= r)   { tmp[k++] = a[j++]; smv(1); }
    for (i = l; i <= r; i++) { a[i] = tmp[i]; smv(1); }
}

void mergeSort(int *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "归并");
    SORT_ENTER(st);
    if (n > 1) {
        int *tmp = (int *)salloc(sizeof(int) * (size_t)n);
        if (tmp) {
            merge_rec(a, tmp, 0, n - 1, order);
            sfree(tmp);
        }
    }
    SORT_EXIT();
}

/* ============================================================
 * 工具函数
 * ============================================================ */

int checkSorted(const int *a, int n, int order) {
    for (int i = 1; i < n; i++) {
        if (order == 1 && a[i - 1] > a[i]) return 0;
        if (order == -1 && a[i - 1] < a[i]) return 0;
    }
    return 1;
}

#endif /* SORTCORE_H */
