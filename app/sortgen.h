// sortgen.h —— 八种排序算法的泛型版本（K = int / long long / ...）
//
// 与 common/sortcore.h 的关系：sortcore.h 是"双语核心"（C/C++ 同源编译，
// 专供第 1~7 点的 int 键矩阵与 C vs C++ 语言对比实验，算法逻辑与计数口径
// 在此冻结以保证结果可复现）；本文件把同一套算法以 C++ 模板泛型化，
// 供第 8 点应用处理 int64 posting 键（(movieId<<20)|(userId<<2)|ratingIdx）。
// 算法逻辑、优化点、计数口径（一次比较记 1；赋值记 1；三赋值交换记 3；
// 基数排序散射与拷回按元素计入）与 sortcore.h 完全一致。
//
// 模块状态（ctx<K>）按 K 类型实例化，与 sortcore.h 的 int 版全局状态互不干扰，
// 二者可在同一编译单元共存（server.cpp 同时使用两套）。
#pragma once

#include <cstdlib>
#include <cstring>
#include <cmath>
#include <type_traits>
#include "../common/sortstats.h"

namespace sg {

template <typename K>
struct Ctx {
    SortStats *ss = nullptr;      /* NULL = 计时模式（不计数） */
    long long  aux_cur = 0;       /* 当前附加堆字节 */
    unsigned   qrng = 2463534242u;/* 快排内部随机源（按 K 独立） */
};

template <typename K>
inline Ctx<K>& ctx() { static Ctx<K> c; return c; }

template <typename K>
inline int scmp(K x, K y, int order) {
    if (ctx<K>().ss) ctx<K>().ss->compares++;
    if (order == 1) return (x > y) - (x < y);
    else            return (y > x) - (y < x);
}

template <typename K>
inline void smv(int k) {
    if (ctx<K>().ss) ctx<K>().ss->moves += k;
}

/* 受登记的堆分配（16 字节头记录块大小，支持嵌套），峰值进 st->aux_peak */
template <typename K>
inline void *salloc(size_t bytes) {
    unsigned char *raw = (unsigned char *)std::malloc(bytes + 16);
    if (!raw) return nullptr;
    std::memcpy(raw, &bytes, sizeof(size_t));
    ctx<K>().aux_cur += (long long)bytes;
    if (ctx<K>().ss && ctx<K>().aux_cur > ctx<K>().ss->aux_peak)
        ctx<K>().ss->aux_peak = ctx<K>().aux_cur;
    return raw + 16;
}

template <typename K>
inline void sfree(void *p) {
    if (!p) return;
    unsigned char *raw = (unsigned char *)p - 16;
    size_t bytes;
    std::memcpy(&bytes, raw, sizeof(size_t));
    ctx<K>().aux_cur -= (long long)bytes;
    std::free(raw);
}

template <typename K>
inline void rng_reseed(unsigned s) { ctx<K>().qrng = s ? s : 1u; }

template <typename K>
inline unsigned xrnd() {
    unsigned x = ctx<K>().qrng;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    return ctx<K>().qrng = x;
}

template <typename K>
inline void sort_enter(SortStats *st) { ctx<K>().ss = st; }
template <typename K>
inline void sort_exit() { ctx<K>().ss = nullptr; }

/* ============================ 实验内容3：简单排序 ============================ */

template <typename K>
void insertionSort(K *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "直接插入");
    sort_enter<K>(st);
    for (int i = 1; i < n; i++) {
        K key = a[i]; smv<K>(1);
        int j = i - 1;
        while (j >= 0 && scmp<K>(a[j], key, order) > 0) {
            a[j + 1] = a[j]; smv<K>(1);
            j--;
        }
        a[j + 1] = key; smv<K>(1);
    }
    sort_exit<K>();
}

template <typename K>
void bubbleSort(K *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "冒泡");
    sort_enter<K>(st);
    for (int i = 0; i < n - 1; i++) {
        int swapped = 0;
        for (int j = 0; j < n - 1 - i; j++) {
            if (scmp<K>(a[j], a[j + 1], order) > 0) {
                K t = a[j]; a[j] = a[j + 1]; a[j + 1] = t;
                smv<K>(3);
                swapped = 1;
            }
        }
        if (!swapped) break;
    }
    sort_exit<K>();
}

template <typename K>
void selectSort(K *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "选择");
    sort_enter<K>(st);
    for (int i = 0; i < n - 1; i++) {
        int sel = i;
        for (int j = i + 1; j < n; j++) {
            if (scmp<K>(a[j], a[sel], order) < 0) sel = j;
        }
        if (sel != i) {
            K t = a[i]; a[i] = a[sel]; a[sel] = t;
            smv<K>(3);
        }
    }
    sort_exit<K>();
}

/* ============================ 实验内容4：高级排序 ============================ */

template <typename K>
void shellSort(K *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "希尔");
    sort_enter<K>(st);
    for (int gap = n / 2; gap > 0; gap /= 2) {
        for (int i = gap; i < n; i++) {
            K key = a[i]; smv<K>(1);
            int j = i - gap;
            while (j >= 0 && scmp<K>(a[j], key, order) > 0) {
                a[j + gap] = a[j]; smv<K>(1);
                j -= gap;
            }
            a[j + gap] = key; smv<K>(1);
        }
    }
    sort_exit<K>();
}

template <typename K>
static void insort_range(K *a, int l, int r, int order) {
    for (int i = l + 1; i <= r; i++) {
        K key = a[i]; smv<K>(1);
        int j = i - 1;
        while (j >= l && scmp<K>(a[j], key, order) > 0) {
            a[j + 1] = a[j]; smv<K>(1);
            j--;
        }
        a[j + 1] = key; smv<K>(1);
    }
}

template <typename K>
static void quick_rec(K *a, int l, int r, int order) {
    while (r - l > 16) {
        int pi = l + (int)(xrnd<K>() % (unsigned)(r - l + 1));
        { K t = a[pi]; a[pi] = a[l]; a[l] = t; smv<K>(3); }
        K pivot = a[l]; smv<K>(1);

        int lt = l, gt = r, i = l + 1;
        while (i <= gt) {
            int cmp = scmp<K>(a[i], pivot, order);
            if (cmp < 0) {
                K t = a[lt]; a[lt] = a[i]; a[i] = t; smv<K>(3); lt++; i++;
            } else if (cmp > 0) {
                K t = a[i]; a[i] = a[gt]; a[gt] = t; smv<K>(3); gt--;
            } else {
                i++;
            }
        }
        if (lt - l < r - gt) {
            quick_rec<K>(a, l, lt - 1, order);
            l = gt + 1;
        } else {
            quick_rec<K>(a, gt + 1, r, order);
            r = lt - 1;
        }
    }
    insort_range<K>(a, l, r, order);
}

template <typename K>
void quickSort(K *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "快速");
    sort_enter<K>(st);
    if (n > 1) quick_rec<K>(a, 0, n - 1, order);
    sort_exit<K>();
}

template <typename K>
static void siftDown(K *a, int start, int end, int order) {
    int i = start, j = 2 * i + 1;
    K tmp = a[i]; smv<K>(1);
    while (j <= end) {
        if (j + 1 <= end && scmp<K>(a[j], a[j + 1], order) < 0) j++;
        if (scmp<K>(tmp, a[j], order) >= 0) break;
        a[i] = a[j]; smv<K>(1);
        i = j; j = 2 * i + 1;
    }
    a[i] = tmp; smv<K>(1);
}

template <typename K>
void heapSort(K *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "堆");
    sort_enter<K>(st);
    for (int i = n / 2 - 1; i >= 0; i--)
        siftDown<K>(a, i, n - 1, order);
    for (int i = n - 1; i > 0; i--) {
        K t = a[0]; a[0] = a[i]; a[i] = t; smv<K>(3);
        siftDown<K>(a, 0, i - 1, order);
    }
    sort_exit<K>();
}

/* 基数排序：LSD 按字节，sizeof(K) 趟；有符号 K 最高字节翻转符号位。
 * compares 恒为 0。降序 = 升序结果原地反转。 */
template <typename K>
void radixSort(K *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "基数");
    sort_enter<K>(st);
    if (n > 1) {
        K *tmp = (K *)salloc<K>(sizeof(K) * (size_t)n);
        if (!tmp) { sort_exit<K>(); return; }
        unsigned char *bytes = (unsigned char *)a;
        constexpr int NPASS = (int)sizeof(K);
        for (int byteIdx = 0; byteIdx < NPASS; byteIdx++) {
            int bucketCount[256];
            std::memset(bucketCount, 0, sizeof(bucketCount));
            for (int i = 0; i < n; i++) {
                unsigned char b = bytes[(size_t)i * NPASS + byteIdx];
                if (byteIdx == NPASS - 1 && std::is_signed_v<K>) b ^= 0x80;
                bucketCount[b]++;
            }
            for (int k = 1; k < 256; k++) bucketCount[k] += bucketCount[k - 1];
            for (int i = n - 1; i >= 0; i--) {
                unsigned char b = bytes[(size_t)i * NPASS + byteIdx];
                if (byteIdx == NPASS - 1 && std::is_signed_v<K>) b ^= 0x80;
                tmp[--bucketCount[b]] = a[i]; smv<K>(1);
            }
            std::memcpy(a, tmp, sizeof(K) * (size_t)n);
            smv<K>(n);   /* 整块拷回按逐元素赋值计 n 次移动（每轮） */
            bytes = (unsigned char *)a;
        }
        sfree<K>(tmp);
        if (order == -1) {
            for (int i = 0, j = n - 1; i < j; i++, j--) {
                K t = a[i]; a[i] = a[j]; a[j] = t; smv<K>(3);
            }
        }
    }
    sort_exit<K>();
}

template <typename K>
static void merge_rec(K *a, K *tmp, int l, int r, int order) {
    if (l >= r) return;
    int mid = l + (r - l) / 2;
    merge_rec<K>(a, tmp, l, mid, order);
    merge_rec<K>(a, tmp, mid + 1, r, order);
    int i = l, j = mid + 1, k = l;
    while (i <= mid && j <= r) {
        if (scmp<K>(a[i], a[j], order) <= 0) tmp[k++] = a[i++];
        else                                 tmp[k++] = a[j++];
        smv<K>(1);
    }
    while (i <= mid) { tmp[k++] = a[i++]; smv<K>(1); }
    while (j <= r)   { tmp[k++] = a[j++]; smv<K>(1); }
    for (i = l; i <= r; i++) { a[i] = tmp[i]; smv<K>(1); }
}

template <typename K>
void mergeSort(K *a, int n, int order, SortStats *st) {
    if (st) stats_init(st, order, "归并");
    sort_enter<K>(st);
    if (n > 1) {
        K *tmp = (K *)salloc<K>(sizeof(K) * (size_t)n);
        if (tmp) {
            merge_rec<K>(a, tmp, 0, n - 1, order);
            sfree<K>(tmp);
        }
    }
    sort_exit<K>();
}

/* ============================ 工具 ============================ */

template <typename K>
int checkSorted(const K *a, int n, int order) {
    for (int i = 1; i < n; i++) {
        if (order == 1 && a[i - 1] > a[i]) return 0;
        if (order == -1 && a[i - 1] < a[i]) return 0;
    }
    return 1;
}

/* ============================ 基准测量（K 版） ============================ */

template <typename K>
using SortFnK = void (*)(K *a, int n, int order, SortStats *st);

struct BenchTimeK {
    double min_ms, med_ms, std_ms;
    int reps, batch;
};

/* 与 common/bench.h 同一套方法学（等时长批量 + 工作集上限 + min/med/std），
 * 但数据来源是调用方给定的 base 数组（真实数据不可"重新生成"，
 * 各拷贝为 base 的逐字节复制 —— 同一批内的相同拷贝对公平性无损）。 */
template <typename K>
BenchTimeK bench_time_k(SortFnK<K> fn, const K *base, int n, int order) {
    BenchTimeK r; r.min_ms = r.med_ms = r.std_ms = -1.0; r.reps = 5; r.batch = 1;
    const int REPS = 5;
    const double TARGET_MS = 8.0, WORKSET = 8.0 * 1024 * 1024;

    /* 预热兼估计（同时触发 AdaptSort 的按类型一次性标定） */
    K *warm = (K *)std::malloc(sizeof(K) * (size_t)(n > 0 ? n : 1));
    if (!warm) return r;
    std::memcpy(warm, base, sizeof(K) * (size_t)n);
    double t0 = now_ms();
    fn(warm, n, order, nullptr);
    double t1 = now_ms();
    std::free(warm);
    double est = t1 - t0;
    if (!(est > 1e-9)) est = 1e-9;

    long long B = (long long)(TARGET_MS / est) + 1;
    if (B > 4096) B = 4096;
    while (B > 1 && (double)B * (double)sizeof(K) * (double)n > WORKSET) B--;
    r.batch = (int)B;

    K *buf = (K *)std::malloc(sizeof(K) * (size_t)n * (size_t)B);
    if (!buf) { B = 1; r.batch = 1; buf = (K *)std::malloc(sizeof(K) * (size_t)n); if (!buf) return r; }
    double per[REPS];
    for (int rep = 0; rep < REPS; rep++) {
        for (long long i = 0; i < B; i++)
            std::memcpy(buf + (size_t)i * n, base, sizeof(K) * (size_t)n);
        double ta = now_ms();
        for (long long i = 0; i < B; i++)
            fn(buf + (size_t)i * n, n, order, nullptr);
        double tb = now_ms();
        per[rep] = (tb - ta) / (double)B;
    }
    std::free(buf);

    /* min / median / std */
    double srt[REPS];
    for (int i = 0; i < REPS; i++) srt[i] = per[i];
    for (int i = 0; i < REPS; i++) for (int j = i + 1; j < REPS; j++)
        if (srt[j] < srt[i]) { double t = srt[i]; srt[i] = srt[j]; srt[j] = t; }
    double sum = 0, sq = 0;
    for (int i = 0; i < REPS; i++) { sum += per[i]; sq += per[i] * per[i]; }
    double mean = sum / REPS;
    double var = sq / REPS - mean * mean;
    if (var < 0) var = 0;
    r.min_ms = srt[0]; r.med_ms = srt[REPS / 2]; r.std_ms = sqrt(var);
    return r;
}

template <typename K>
int bench_count_k(SortFnK<K> fn, const K *base, int n, int order,
                  const char *name, SortStats *st, double *tcnt_ms) {
    K *a = (K *)std::malloc(sizeof(K) * (size_t)(n > 0 ? n : 1));
    if (!a) return 0;
    std::memcpy(a, base, sizeof(K) * (size_t)n);
    rng_reseed<K>(0x2545F491u);
    stats_init(st, order, name);
    double t0 = now_ms();
    fn(a, n, order, st);
    double t1 = now_ms();
    int ok = checkSorted<K>(a, n, order);
    std::free(a);
    if (tcnt_ms) *tcnt_ms = t1 - t0;
    return ok;
}

}  // namespace sg
