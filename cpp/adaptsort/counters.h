// counters.h —— 统计与计时基础设施（对应评分点 5）
//
// 计数约定（全项目统一，务必与报告一致）：
//   * 一次"比较" = 一次关键字之间的 < 运算（含排序算法内部的枢轴比较、边界检查）
//   * 一次"移动" = 一次元素赋值 a[i] = a[j]；一次三赋值交换记 3 次移动
//   * 元素读入局部临时变量（T key = a[i]）也记 1 次移动，与教材口径一致
//   * 计数排序/基数排序不含关键字比较，但把"键提取+越界检查"按 1 次/元素记入比较，
//     否则这两个算法会显示为 0 次比较，与其他算法不可比
//
// 纯计数模式与计时模式分离：计时时关闭计数（counters().on = false），
// 避免计数器自增本身污染时间测量。
#pragma once

#include <cstddef>

namespace sb {

struct Counters {
    unsigned long long cmp = 0;
    unsigned long long mv  = 0;
    bool on = false;
    void reset() { cmp = 0; mv = 0; }
};

inline Counters& counters() { static Counters c; return c; }

// RAII：进入作用域设置计数开关，退出时恢复
struct CountScope {
    bool prev;
    explicit CountScope(bool enable) : prev(counters().on) { counters().on = enable; }
    ~CountScope() { counters().on = prev; }
    CountScope(const CountScope&) = delete;
    CountScope& operator=(const CountScope&) = delete;
};

inline void tick_cmp() { if (counters().on) ++counters().cmp; }
inline void tick_mv(unsigned long long k = 1) { if (counters().on) counters().mv += k; }

// ---------------- 峰值附加空间统计 ----------------
// 只统计算法自身申请的堆空间（不含输入数组本身），用于验证 O(1)/O(n)/O(n+k) 论断
struct MemTracker {
    std::size_t cur = 0;
    std::size_t peak = 0;
    void add(std::size_t b) { cur += b; if (cur > peak) peak = cur; }
    void sub(std::size_t b) { cur -= b; }
    void reset() { cur = 0; peak = 0; }
};

inline MemTracker& mem() { static MemTracker m; return m; }

// 受跟踪的缓冲区：构造即登记，析构即注销
template <typename T>
struct Buf {
    T* p = nullptr;
    std::size_t n = 0;

    Buf() = default;
    explicit Buf(std::size_t count, bool zero = false) : p(nullptr), n(count) {
        p = zero ? new T[count]() : new T[count];
        mem().add(count * sizeof(T));
    }
    ~Buf() {
        if (p) { mem().sub(n * sizeof(T)); delete[] p; }
    }
    Buf(const Buf&) = delete;
    Buf& operator=(const Buf&) = delete;

    T* data() { return p; }
    const T* data() const { return p; }
};

}  // namespace sb
