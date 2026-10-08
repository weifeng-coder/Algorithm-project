// algorithms.h —— 8 个指定排序算法的自实现（对应评分点 3、4）
//
// 设计原则：每个基线都按"教材标准实现 + 常见工程优化"来写，不做稻草人。
// 优化点逐条注释，便于报告中说明"对手有多强"。
// 全部按升序实现；降序由 AdaptSort 的 reverse 分支或比较器取反得到。
#pragma once

#include "counters.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <type_traits>
#include <vector>

namespace sb {

// ---------------- 带计数的基本操作 ----------------
template <typename T>
inline bool lt(const T& x, const T& y) { tick_cmp(); return x < y; }

template <typename T>
inline void mv(T& dst, const T& src) { tick_mv(); dst = src; }

template <typename T>
inline void swp(T& x, T& y) { tick_mv(3); T t = x; x = y; y = t; }

// ---------------- 1. 直接插入排序 ----------------
// 优化：若本轮元素本就到位（j+1 == i），不做回写，正序数据下移动次数为 0
template <typename T>
void insertion_sort(T* a, int n) {
    for (int i = 1; i < n; ++i) {
        T key = a[i]; tick_mv();
        int j = i - 1;
        while (j >= 0 && lt(key, a[j])) { mv(a[j + 1], a[j]); --j; }
        if (j + 1 != i) mv(a[j + 1], key);
    }
}

// ---------------- 2. 冒泡排序 ----------------
// 优化：带 swapped 标志提前退出，正序数据下退化为 1 趟 = n-1 次比较
template <typename T>
void bubble_sort(T* a, int n) {
    for (int i = n - 1; i > 0; --i) {
        bool swapped = false;
        for (int j = 0; j < i; ++j)
            if (lt(a[j + 1], a[j])) { swp(a[j], a[j + 1]); swapped = true; }
        if (!swapped) break;
    }
}

// ---------------- 3. 选择排序 ----------------
// 特点：比较次数恒为 n(n-1)/2，移动次数最少（≤ n-1 次交换）
template <typename T>
void selection_sort(T* a, int n) {
    for (int i = 0; i < n - 1; ++i) {
        int m = i;
        for (int j = i + 1; j < n; ++j)
            if (lt(a[j], a[m])) m = j;
        if (m != i) swp(a[i], a[m]);
    }
}

// ---------------- 4. 希尔排序 ----------------
// 增量序列：Knuth 序列 1, 4, 13, 40, ... (h = 3h+1)，最坏 O(n^1.5)
template <typename T>
void shell_sort(T* a, int n) {
    int h = 1;
    while (h < n / 3) h = 3 * h + 1;
    for (; h >= 1; h /= 3) {
        for (int i = h; i < n; ++i) {
            T key = a[i]; tick_mv();
            int j = i;
            while (j >= h && lt(key, a[j - h])) { mv(a[j], a[j - h]); j -= h; }
            if (j != i) mv(a[j], key);
        }
    }
}

// ---------------- 5. 快速排序 ----------------
// 优化：三数取中选枢轴 + Hoare 二路划分 + 小区间阈值 16 + 尾递归消除
// 收尾：对整个数组做一趟插入排序。原理是划分后每个元素距其最终位置不超过阈值 16，
//       故收尾代价为 O(16n) 而非 O(n^2)（Sedgewick 的经典做法）
static const int QUICK_CUTOFF = 16;

template <typename T>
void quick_sort_rec(T* a, int lo, int hi) {
    while (hi - lo > QUICK_CUTOFF) {
        int mid = lo + (hi - lo) / 2;
        if (lt(a[mid], a[lo])) swp(a[mid], a[lo]);
        if (lt(a[hi], a[lo]))  swp(a[hi], a[lo]);
        if (lt(a[hi], a[mid])) swp(a[hi], a[mid]);   // a[lo] <= a[mid] <= a[hi]
        T pivot = a[mid]; tick_mv();
        int i = lo, j = hi;
        while (i <= j) {
            while (lt(a[i], pivot)) ++i;
            while (lt(pivot, a[j])) --j;
            if (i <= j) { swp(a[i], a[j]); ++i; --j; }
        }
        // 先递归较小一侧，迭代较大一侧，栈深 O(log n)
        if (j - lo < hi - i) { quick_sort_rec(a, lo, j); lo = i; }
        else                 { quick_sort_rec(a, i, hi); hi = j; }
    }
}

template <typename T>
void quick_sort(T* a, int n) {
    if (n < 2) return;
    quick_sort_rec(a, 0, n - 1);
    insertion_sort(a, n);   // 收尾：一趟插入排序
}

// ---------------- 6. 堆排序 ----------------
// 自底向上建堆（Floyd 建堆，O(n)）+ 自顶向下筛选
template <typename T>
void sift_down(T* a, int start, int end) {   // 区间 [start, end)
    int root = start;
    while (2 * root + 1 < end) {
        int child = 2 * root + 1;
        if (child + 1 < end && lt(a[child], a[child + 1])) ++child;
        if (lt(a[root], a[child])) { swp(a[root], a[child]); root = child; }
        else return;
    }
}

template <typename T>
void heap_sort(T* a, int n) {
    for (int i = n / 2 - 1; i >= 0; --i) sift_down(a, i, n);
    for (int e = n - 1; e > 0; --e) { swp(a[0], a[e]); sift_down(a, 0, e); }
}

// ---------------- 7. 基数排序（LSD，16 位数字） ----------------
// 有符号整数按"符号位取反"映射为无符号键，保持序关系（无需知道 min）
template <typename T>
inline std::make_unsigned_t<T> to_ukey(T v) {
    using U = std::make_unsigned_t<T>;
    U u = static_cast<U>(v);
    if constexpr (std::is_signed_v<T>) u ^= (U(1) << (sizeof(T) * 8 - 1));
    return u;
}

// bits：有效位宽（只跑必要的趟数）；传 0 表示按整个类型宽度
// DB：数字位宽（模板参数）。DB=16 桶表 256KB（只能驻 L2），趟数少；
//     DB=8  桶表 1KB（恒驻 L1），趟数多。两者优劣强依赖 n 与位宽，
//     由 AdaptSort 的成本模型按本机标定选择（见 adaptsort.h v3.1 注记）。
template <typename T, int DB = 16>
void radix_sort_lsd(T* a, int n, int bits = 0) {
    static_assert(std::is_integral_v<T>, "radix sort requires integral key");
    static_assert(DB == 8 || DB == 16, "digit width must be 8 or 16");
    if (n < 2) return;
    const std::size_t NB = std::size_t(1) << DB;
    if (bits <= 0) bits = int(sizeof(T) * 8);
    int passes = (bits + DB - 1) / DB;
    if (passes < 1) passes = 1;

    Buf<T> tmp(n);
    Buf<unsigned int> cntb(NB, true);
    unsigned int* cnt = cntb.data();
    T* src = a;
    T* dst = tmp.data();

    for (int p = 0; p < passes; ++p) {
        const int shift = p * DB;
        std::memset(cnt, 0, NB * sizeof(unsigned int));
        for (int i = 0; i < n; ++i) { tick_cmp(); ++cnt[(std::size_t(to_ukey(src[i])) >> shift) & (NB - 1)]; }
        unsigned int sum = 0;
        for (std::size_t i = 0; i < NB; ++i) { unsigned int t = cnt[i]; cnt[i] = sum; sum += t; }
        for (int i = 0; i < n; ++i) {
            std::size_t d = (std::size_t(to_ukey(src[i])) >> shift) & (NB - 1);
            mv(dst[cnt[d]++], src[i]);
        }
        std::swap(src, dst);
    }
    if (src != a) for (int i = 0; i < n; ++i) mv(a[i], src[i]);
}

// ---------------- 8. 归并排序（自底向上迭代版） ----------------
// 优化：边界有序检测（a[mid-1] <= a[mid] 时跳过合并）+ 半区正反复制免边界判断
template <typename T>
void merge_runs(T* a, T* tmp, int lo, int mid, int hi) {
    for (int i = lo; i < mid; ++i) mv(tmp[i], a[i]);
    for (int i = mid; i < hi; ++i) mv(tmp[i], a[hi - 1 - (i - mid)]);   // 后半区倒序复制
    int i = lo, j = hi - 1;
    for (int k = lo; k < hi; ++k) {
        if (lt(tmp[j], tmp[i])) { mv(a[k], tmp[j]); --j; }
        else                    { mv(a[k], tmp[i]); ++i; }
    }
}

template <typename T>
void merge_sort(T* a, int n) {
    if (n < 2) return;
    Buf<T> tmp(n);
    for (int w = 1; w < n; w *= 2) {
        for (int lo = 0; lo < n; lo += 2 * w) {
            int mid = std::min(lo + w, n), hi = std::min(lo + 2 * w, n);
            if (mid >= hi) continue;
            tick_cmp();
            if (!(a[mid] < a[mid - 1])) continue;   // 两段已整体有序，跳过合并
            merge_runs(a, tmp.data(), lo, mid, hi);
        }
    }
}

// ================= 对照组变体（附录用，不属于 8 个指定算法） =================

// 变体 A：三路划分快速排序（用于证明 AdaptSort 在高重复率上的增益来自三路划分本身）
template <typename T>
void quick3_rec(T* a, int lo, int hi) {
    while (hi - lo > QUICK_CUTOFF) {
        int mid = lo + (hi - lo) / 2;
        if (lt(a[mid], a[lo])) swp(a[mid], a[lo]);
        if (lt(a[hi], a[lo]))  swp(a[hi], a[lo]);
        if (lt(a[hi], a[mid])) swp(a[hi], a[mid]);
        T pivot = a[mid]; tick_mv();
        int lt_i = lo, gt_i = hi, i = lo;
        while (i <= gt_i) {
            if (lt(a[i], pivot))      { swp(a[lt_i++], a[i++]); }
            else if (lt(pivot, a[i])) { swp(a[i], a[gt_i--]); }
            else                      { ++i; }
        }
        if (lt_i - lo < hi - gt_i) { quick3_rec(a, lo, lt_i - 1); lo = gt_i + 1; }
        else                       { quick3_rec(a, gt_i + 1, hi); hi = lt_i - 1; }
    }
}

template <typename T>
void quick_sort_3way(T* a, int n) {
    if (n < 2) return;
    quick3_rec(a, 0, n - 1);
    insertion_sort(a, n);
}

// 变体 B：自然归并（先识别已有的升序 run，再按 run 归并）——Timsort 的核心思想
// run_end(s) 返回从 s 开始的升序 run 的结束位置（不含）
template <typename T>
inline int run_end(const T* a, int s, int n) {
    int j = s;
    while (j + 1 < n && !lt(a[j + 1], a[j])) ++j;
    return j + 1;
}

template <typename T>
void natural_merge_sort(T* a, int n) {
    if (n < 2) return;
    Buf<T> tmp(n);
    while (true) {
        int i = 0;
        bool merged = false;
        while (i < n) {
            int r1 = run_end(a, i, n);
            if (r1 >= n) break;                       // 只剩一个 run，整体已有序
            int r2 = run_end(a, r1, n);
            merge_runs(a, tmp.data(), i, r1, r2);     // 合并相邻两个 run
            merged = true;
            i = r2;
        }
        if (!merged) break;
    }
}

// 变体 D：run 感知的自底向上归并（AdaptSort 的策略 S7）
// 与上面的自然归并相比的关键差别：run 边界只扫一次并记录下来，
// 之后每一趟直接按记录的边界归并，不再重新扫描数组找 run。
// 近似有序数据（真实 CVE 语料就是这种形态）下，这一步省掉的是 O(n log r) 次重复扫描。
template <typename T>
void run_merge_sort(T* a, int n) {
    if (n < 2) return;

    // 1) 一次线性扫描，记录所有自然升序 run 的边界：run k 覆盖 [bnd[k], bnd[k+1])
    std::vector<int> bnd;
    bnd.reserve(64);
    bnd.push_back(0);
    for (int i = 1; i < n; ++i) { tick_cmp(); if (a[i] < a[i - 1]) bnd.push_back(i); }
    bnd.push_back(n);
    if (bnd.size() <= 2) return;      // 整体已有序，零移动

    // 2) 按 run 边界自底向上两两归并
    Buf<T> tmp(n);
    std::vector<int> nb;
    while (bnd.size() > 2) {
        const int runs = int(bnd.size()) - 1;
        nb.clear();
        nb.push_back(0);
        int i = 0;
        while (i < runs) {
            if (i + 1 < runs) {
                merge_runs(a, tmp.data(), bnd[i], bnd[i + 1], bnd[i + 2]);
                nb.push_back(bnd[i + 2]);
                i += 2;
            } else {
                nb.push_back(bnd[i + 1]);   // 落单的 run 原样晋级
                i += 1;
            }
        }
        bnd.swap(nb);
    }
}

// 变体 C：内省快速排序（快排 + 递归深度超限转堆排序）——AdaptSort 的通用兜底
// 结构与 quick_sort_rec 完全一致（较小一侧递归、较大一侧迭代），
// 只在每层划分前多一次 depth 判断，避免为最坏情况保护付可观常数
template <typename T>
void heap_sort_range(T* a, int lo, int hi) {   // 区间 [lo, hi)
    const int n = hi - lo;
    for (int i = n / 2 - 1; i >= 0; --i) sift_down(a + lo, i, n);
    for (int e = n - 1; e > 0; --e) { swp(a[lo], a[lo + e]); sift_down(a + lo, 0, e); }
}

template <typename T>
void intro_rec(T* a, int lo, int hi, int depth) {
    while (hi - lo > QUICK_CUTOFF) {
        if (depth <= 0) { heap_sort_range(a, lo, hi + 1); return; }   // O(n log n) 最坏界
        --depth;
        int mid = lo + (hi - lo) / 2;
        if (lt(a[mid], a[lo])) swp(a[mid], a[lo]);
        if (lt(a[hi], a[lo]))  swp(a[hi], a[lo]);
        if (lt(a[hi], a[mid])) swp(a[hi], a[mid]);
        T pivot = a[mid]; tick_mv();
        int i = lo, j = hi;
        while (i <= j) {
            while (lt(a[i], pivot)) ++i;
            while (lt(pivot, a[j])) --j;
            if (i <= j) { swp(a[i], a[j]); ++i; --j; }
        }
        if (j - lo < hi - i) { intro_rec(a, lo, j, depth); lo = i; }
        else                 { intro_rec(a, i, hi, depth); hi = j; }
    }
}

template <typename T>
void intro_sort(T* a, int n) {
    if (n < 2) return;
    int depth = 2 * int(std::log2(double(n) > 1.0 ? double(n) : 2.0));
    intro_rec(a, 0, n - 1, depth);
    insertion_sort(a, n);
}

}  // namespace sb
