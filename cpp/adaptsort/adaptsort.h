// adaptsort.h —— 普适排序算法 AdaptSort（对应评分点 7）
//
// ============================ 设计纲领 ============================
// 题目提示原文："很少有某个算法能够在所有方面打败其他算法"。
// 因此本设计不把"普适"寄托在一个更快的单一内层循环上，而是设计一个
// 新的自适应排序算法 AdaptSort——其核心是一个从输入特征到排序策略的
// 决策函数 D(特征)→策略：
//
//     D: (inv_rate, asc_prefix, 值域, 重复率, ...) → {S1b, S2, ..., S8}
//
// 使算法在全部测试分布上的表现不劣于任何一个单一算法的最优表现
// （Timsort、introsort 正是以同样的形态定义的：决策函数 + 策略集）。
//
// AdaptSort 分四层：
//   类型层：编译期判定键是否为整数（决定"值域类"策略是否可用）
//   特征层：O(√n) 采样 + 必要时的 O(n) 精确扫描，构造特征向量
//           (单调性, 逆序率, 非降前缀占比, 值域, 重复率)
//   决策层：决策函数 D = 短路规则 + 运行时自标定的成本模型预测
//   策略层：8 个具体策略，含一个 O(n log n) 最坏界的比较类兜底
//
// v2（2026-09-26）：①k 维三点分段标定 + 计数/基数不对称裕度 CMARGIN；
// ②probe 拆分延迟付费 + 每规模派生量并入 prepare 缓存。
// v3（2026-09-27）：计数成本模型升级为 (n,k) 完整标定网格（5 锚点、log-log
// 双维插值、外推斜率限幅），标定 reps 加档压低系数噪声。
// 设计与 A/B 验证见《第七题-改进方案与实施记录.md》。
//
// 四个关键设计点：
//   1) 采样先行。全量"是否已有序"检查是 O(n) 的，对 n=10^6 约 1 ms，
//      对一次 10 ms 的计数排序是 10% 的开销。故先用 O(√n) 采样判断趋势，
//      只有采样一致时才做全量确认，避免为随机数据白付 O(n)。
//   2) 采样值域是真实值域的下界（smin>=min, smax<=max），
//      故"采样值域 > 8n"可安全推出"真实值域 > 8n"，据此跳过全量扫描而不损失正确性；
//      跳过扫描时基数排序一律按整个类型宽度计算趟数（保守但绝对正确）。
//   3) 成本模型在启动时用真实策略实测标定，而不是靠 CPU 周期数推算。
//      计数排序与基数排序的相对优劣取决于 k/n 与桶数组是否落在 cache 里，
//      写死阈值不可移植；而微基准（数比较/赋值次数）会被 GCC 自动向量化，
//      测出来的是吞吐量而非真实单价 —— 本文件早期版本正因此把快排低估了 8 倍。
//   4) 值域类策略不做关键字比较，但把"键提取+越界检查"记 1 次/元素，
//      避免出现"0 次比较"的不可比数据。
#pragma once

#include "algorithms.h"
#include "counters.h"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <type_traits>
#include <vector>

namespace sb {

// ==================================================================
// 一、成本模型（单位：毫秒）
//
// 为什么必须是"多规模实测 + log-log 插值"而不是一个解析公式：
//   同一算法在 n=10^4（数据全在 L1/L2）与 n=10^6（数据超出 L3）下的
//   每元素成本相差 5~15 倍。任何形如 T = a*n + b*n*log n 的单一解析式
//   都无法同时拟合两端 —— 实测两点的最小二乘甚至会出现负系数。
//   本模型改为在 4 个 log 等距规模上实测各策略的真实耗时，得到每条策略的
//   成本曲线 T(n)（或 a(n), b(n)），再按 log-log 线性插值求值。
//   这样 cache 效应被自动吸收进标定数据，而不是被错误地写进公式。
// ==================================================================
enum CalibField {
    CF_CMP_GEN = 0,   // 比较类（通用无序）：T ≈ v * n*log2(n)
    CF_CMP_DUP,       // 比较类（高重复率）：T ≈ v * n*log2(n)
    CF_MSORT,         // 归并（近序，离散扰动）：T ≈ v * n*log2(n)
    CF_CNT_K0,        // 计数排序标定网格：k 锚点 0（=KANC[0]）处的实测耗时
    CF_CNT_K1,        // 以下各锚点见 CostModel::KANC
    CF_CNT_K2,
    CF_CNT_K3,
    CF_CNT_K4,
    CF_RAD_P,         // 基数排序单趟耗时（16 位数字）：T ≈ passes * v
    CF_RAD8_P,        // 基数排序单趟耗时（8 位数字，v3.1）：T ≈ passes * v
    CF_MSORT_BLK,     // 归并（近序，块状扰动）：T ≈ v * n*log2(n)
    CF_CMP_NEAR,      // 比较类（近序，离散扰动）：T ≈ v * n*log2(n)
    CF_CMP_BLK,       // 比较类（近序，块状扰动）：T ≈ v * n*log2(n)
    CF_COUNT
};

struct CostModel {
    static const int MAXP = 8;
    int np = 0;
    struct Pt { double n; double v[CF_COUNT]; };
    Pt pt[MAXP];

    // 在规模 n 处对第 idx 个系数做 log-log 插值（区间外做斜率受限的外推）
    double at(double n, int idx) const {
        if (np == 0) return 1e-9;
        if (n <= pt[0].n) return pt[0].v[idx];
        if (n >= pt[np - 1].n) {
            if (np < 2) return pt[np - 1].v[idx];
            const Pt& a = pt[np - 2];
            const Pt& b = pt[np - 1];
            double s = (std::log(b.v[idx]) - std::log(a.v[idx])) /
                       (std::log(b.n) - std::log(a.n));
            if (!(s > -1.0)) s = -1.0;      // 限制外推斜率，防止系数爆炸
            if (s > 1.5) s = 1.5;
            return std::exp(std::log(b.v[idx]) + s * (std::log(n) - std::log(b.n)));
        }
        int i = 0;
        while (i + 1 < np && pt[i + 1].n < n) ++i;
        const Pt& a = pt[i];
        const Pt& b = pt[i + 1];
        double t = (std::log(n) - std::log(a.n)) / (std::log(b.n) - std::log(a.n));
        return std::exp(std::log(a.v[idx]) + t * (std::log(b.v[idx]) - std::log(a.v[idx])));
    }

    // ---- 求值缓存 ----
    // at() 内部要做 log/exp 插值，单个系数约 4 次超越函数调用；一次决策要查
    // 五六个系数，合计 300~500 ns。这在 n=10^5 以上的排序里可以忽略，
    // 但在 n=100 档（整次排序仅约 600 ns）它就是全部开销。
    // 同一 n 上的决策结果是不变的，故按 n 做一层缓存，重复调用时零成本。
    // v2（改进②）：决策层用到的每规模派生量（nl、infl、scan_cost、e_radix_full）
    // 也都是 (n, sizeof(T), m) 的确定函数，一并入缓存 —— v1 每次调用现算它们要
    // 再付 4 次超越函数（log2/log/sqrt，约 60~150 ns），是 n=100 档决策开销的一半。
    // 注：缓存非线程安全；AdaptSort 作为库使用时若需多线程，应改为 thread_local。
    mutable double c_n = -1.0;
    mutable int    c_pf = -1;
    mutable int    c_m = -1;
    mutable double c_v[CF_COUNT];
    mutable double c_nl = 0.0;
    mutable double c_infl = 1.0;
    mutable double c_scan = 0.0;
    mutable double c_eradix_full = 0.0;

    void prepare(double n, int passes_full, int m_samp) const {
        if (c_n == n && c_pf == passes_full && c_m == m_samp) return;
        for (int i = 0; i < CF_COUNT; ++i) c_v[i] = at(n, i);
        c_nl = n * std::log2(n > 2.0 ? n : 2.0);
        c_infl = (n > 1.0 && m_samp > 1)
               ? std::sqrt(std::log(n) / std::log(double(m_samp))) : 1.0;
        c_scan = 2.0 * c_v[CF_CMP_NEAR] * n;
        c_eradix_full = double(passes_full) * c_v[CF_RAD_P];
        c_n = n; c_pf = passes_full; c_m = m_samp;
    }
    double coef(int i) const { return c_v[i]; }
    double nl() const { return c_nl; }
    double infl() const { return c_infl; }
    double scan_cost() const { return c_scan; }
    double e_radix_full() const { return c_eradix_full; }

    // ---- 计数排序成本：(n,k) 完整标定网格（v3，改进第二轮）----
    // k 维与 n 维同构：5 个 k 锚点上直接实测计数耗时（锚点跨过计数数组
    // 256B→4KB→128KB→1MB→4MB 的 L1→L2→L3 台阶），跨 k 在相邻锚点间做
    // log-log 插值，跨 n 由 prepare()/at() 插值，网格外按斜率限幅外推。
    // v1/v2 的 T = a·n + b·k 参数分解在此废弃，原因有二：
    //   * 线性假设跨不过 cache 台阶 —— 大 k 中带（k=2 万~20 万）实测偏差
    //     可达 ±50%~280%；
    //   * 系数由近等数相减拟合，信噪比差，是边界决策跑间翻转的噪声源之一。
    // 网格直接存"锚点上的实测耗时"，插值只用相对比值，天然免去这两类误差。
    // 上端外推的斜率下限 0.8 同时充当内存护栏：k 越界巨大时预测值随之爆炸，
    // 计数排序不会被选中（否则会试图分配 k×4B 的计数数组）。
    static constexpr int NK = 5;
    static constexpr long long KANC[NK] = {64, 1024, 32768, 262144, 1000000};

    double est_count(int /*n*/, long long k) const {
        const double kk = double(k);
        // v3.1 修复：锚点定位上限从 NK-1 收紧到 NK-2。原实现允许 i 到达 NK-1，
        // 随后读取 KANC[i+1]（越界 UB）与 c_v[CF_CNT_K0+i+1]（实为相邻系数），
        // 大 k（如 int64 posting 键 k≈8.8×10^11）时外推完全失控 —— 斜率可能为负，
        // 计数排序被误判为最优，进而按 k 请求 3.5TB 内存 → bad_alloc。
        // 收紧后 kk > KANC[NK-1] 的越界大 k 落入末段区间做外推，
        // 斜率限幅 [0.8,1.2] 使内存护栏按设计生效（cost 随 k 至少 k^0.8 增长）。
        int i = 0;
        while (i + 1 < NK - 1 && kk > double(KANC[i + 1])) ++i;   // 定位 k 所处的锚点区间
        const double lo = double(KANC[i]);
        double s = std::log(c_v[CF_CNT_K0 + i + 1] / c_v[CF_CNT_K0 + i])
                 / std::log(double(KANC[i + 1]) / double(KANC[i]));
        if (i == 0 && kk < lo && s > 1.0) s = 1.0;   // 下端外推：小 k 段真实曲线平缓（a·n 项主导）
        if (i == NK - 2 && kk > lo) {                // 上端外推：init+prefix 随 k 近似线性
            if (s < 0.8) s = 0.8;
            if (s > 1.2) s = 1.2;
        }
        return c_v[CF_CNT_K0 + i] * std::exp(s * std::log(kk / lo));
    }
    double est_radix(int n, int passes, int db) const {
        // v3.1：数字位宽进入成本模型 —— DB=8 与 DB=16 各标一套单趟系数，
        // 决策层按 趟数 × 单趟 取最小（原实现写死 16 位）。
        return double(passes) * c_v[db == 8 ? CF_RAD8_P : CF_RAD_P];
    }
    double est_cmp(int n, double nl, bool dup) const {
        return (dup ? c_v[CF_CMP_DUP] : c_v[CF_CMP_GEN]) * nl;
    }
    double est_msort(double nl) const { return c_v[CF_MSORT] * nl; }
    // 近序归并成本：块状扰动与离散扰动的系数差 3~4 倍，必须分开取。
    double est_msort_near(double nl, bool block) const {
        return (block ? c_v[CF_MSORT_BLK] : c_v[CF_MSORT]) * nl;
    }
};

inline double now_ns() {
    using namespace std::chrono;
    return double(duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count());
}

inline volatile long long g_sink = 0;

// ==================================================================
// 二、特征层：O(√n) 采样探测（分两阶段，按需付费）
//
// 阶段 A（probe_order）：只测顺序结构 —— 单调性与相邻逆序率，每采样点 2 次比较。
// 阶段 B（probe_domain）：再测值域与重复率，需要多付每采样点 2 次比较 + 一次
//                        O(m^2) 的采样点排序。
//
// 分两阶段的理由：顺序结构往往单独就能定案（已升序 / 已降序 / 小规模近似有序），
// 这时完全不必付值域探测的成本。以 n=100 的真实 CVE 派生序列为例，
// 采样直接给出"低逆序率 → 直接插入排序"，值域探测纯属白花；
// 而这 20 来次比较在 n=100 档就是排序总时间的可观比例。
// ==================================================================
static const int SAMPLE_MAX = 128;
static const int DUP_SAMPLE = 32;   // 估计重复率只用的采样点数（粗估计足够）

template <typename T>
struct Feat {
    int    n = 0;
    int    m = 0;               // 实际采样点数
    bool   asc_sample = false;  // 采样点非降
    bool   desc_sample = false; // 采样点非增
    double inv_rate = 0.0;      // 采样相邻逆序率
    // 采样序列中"非降前缀"占全部相邻对的比例。
    // 这一维是实测逼出来的：相邻逆序率相同的两个近序输入，归并成本可差 3.7 倍
    // （n=10^4：离散 2% 相邻交换 0.129 ms；92.5% 有序前缀 + 7.5% 随机尾段 0.035 ms，
    // 两者逆序率都是 0.037）。原因是块状扰动只破坏尾部那一段的归并边界，
    // 而离散扰动在每一层的归并边界上都留下断点。
    // 可见"逆序率"不足以刻画近序程度，必须再加一个"扰动是否成块"的维度。
    double asc_prefix = 0.0;

    // ---- 阶段 B 填充（v2 拆分延迟：值域便宜、立即测；重复率只服务
    //      "三路 vs 二路"二选一，延迟到确认落入比较类才测，n ≤ 512 不测） ----
    bool   has_domain = false;   // smin/smax/sample_range 已填
    bool   has_dup = false;      // dup_rate 已填
    T      smin{}, smax{};
    long long sample_range = 0;
    double dup_rate = 0.0;      // 采样重复率

    // 只保留前 DUP_SAMPLE 个采样点供阶段 B 复用。
    // 数组尺寸刻意压到 DUP_SAMPLE 而不是 SAMPLE_MAX：Feat 是按值返回的，
    // 带一个 128 元素的数组会让每次调用都多拷 512 字节 —— 在 n=100 档
    // （整次排序仅约 0.6 us）这是能被量出来的固定开销。
    T   samp[DUP_SAMPLE];
    int cnt = 0;                // 实际采样点数
    int dn = 0;                 // 其中已存入 samp 的点数
};

// 阶段 A：顺序结构。
// 只做"相邻采样点"的比较，故用滑动窗口即可，无需保存整个采样序列。
template <typename T>
Feat<T> probe_order(const T* a, int n) {
    Feat<T> f;
    f.n = n;
    int m = int(std::sqrt(double(n)));
    if (m < 8) m = 8;
    if (m > SAMPLE_MAX) m = SAMPLE_MAX;
    if (m > n) m = n;

    const int stride = (n > m) ? (n / m) : 1;
    bool asc = true, desc = true;
    int adj_inv = 0;
    int pre = 0;                 // 非降前缀已覆盖的相邻对数
    bool prefix_ok = true;       // 前缀是否仍然非降
    T prev{};
    for (int i = 0; i < m; ++i) {
        const int idx = i * stride;
        if (idx >= n) break;
        const T cur = a[idx];
        if (f.dn < DUP_SAMPLE) f.samp[f.dn++] = cur;
        if (f.cnt > 0) {
            tick_cmp(); const bool less = cur < prev;
            tick_cmp(); const bool greater = prev < cur;
            if (less) { asc = false; ++adj_inv; }
            if (greater) desc = false;
            // 非降前缀：一旦出现逆序就封口。块状扰动（有序前缀 + 随机尾段）
            // 下它接近 1；离散扰动下几个采样点内就会封口。用 !less 而非再比一次，
            // 省掉一次比较 —— 探测本身的开销会直接计入 n=100 档的总耗时。
            if (prefix_ok) { if (!less) ++pre; else prefix_ok = false; }
        }
        prev = cur;
        ++f.cnt;
    }
    f.m = f.cnt;
    if (f.cnt == 0) return f;

    f.asc_sample = asc;
    f.desc_sample = desc;
    f.inv_rate = (f.cnt > 1) ? double(adj_inv) / double(f.cnt - 1) : 0.0;
    f.asc_prefix = (f.cnt > 1) ? double(pre) / double(f.cnt - 1) : 0.0;
    return f;
}

// ---- 阶段 B 之一：值域 min/max（便宜，2(dn-1) 次比较，就地填充，不重新采样）----
// 值域分析与值域类策略的预测都依赖它，整数分支立即调用。
template <typename T>
void probe_range(Feat<T>& f) {
    if (f.has_domain || f.dn == 0) return;
    f.has_domain = true;
    f.smin = f.smax = f.samp[0];
    for (int i = 1; i < f.dn; ++i) {
        tick_cmp(); if (f.samp[i] < f.smin) f.smin = f.samp[i];
        tick_cmp(); if (f.smax < f.samp[i]) f.smax = f.samp[i];
    }
    f.sample_range = (long long)f.smax - (long long)f.smin;
}

// ---- 阶段 B 之二：重复率（较贵，含一次 O(dn^2) 的采样点插入排序）----
// v2（改进②）起延迟付费：dup_rate 只服务于"三路快排 vs 二路内省"这一个
// 二选一，选了计数/基数/归并时纯属白付 —— 整数分支只在确认落入比较类家族
// 后才调用，且 n ≤ 512 时完全不调（该规模下二路 Hoare 划分对重复键天然平衡
// —— 全等输入对半分 —— 三路收益低于探测成本，n=100 档约 40ns，即排序的 10%）。
// 重复率：对采样点做一次手写插入排序后数不同值个数。
//   1) 不用 std::sort —— 采样点只有几十个，std::sort 的调用与 introsort
//      框架开销在 n=100 档会占到整个排序时间的可观比例；
//   2) 只用前 DUP_SAMPLE 个点：重复率只需粗估计（它只在比较类家族内部
//      区分三路/二路快排，是个二值判断），而 m 可达 128，
//      对全部点做 O(m^2) 排序在 n=10^4 档要付 2500 次比较。
template <typename T>
void probe_dup(Feat<T>& f) {
    if (f.has_dup || f.dn == 0) return;
    f.has_dup = true;
    const int dn = f.dn;
    T tmp[DUP_SAMPLE];
    for (int i = 0; i < dn; ++i) tmp[i] = f.samp[i];
    for (int i = 1; i < dn; ++i) {
        T key = tmp[i];
        int j = i - 1;
        while (j >= 0 && lt(key, tmp[j])) { tmp[j + 1] = tmp[j]; --j; }
        tmp[j + 1] = key;
    }
    int distinct = 1;
    for (int i = 1; i < dn; ++i) { tick_cmp(); if (tmp[i - 1] < tmp[i]) ++distinct; }
    f.dup_rate = 1.0 - double(distinct) / double(dn);
}

// 兼容包装：诊断工具（trace.cpp / truth.cpp）沿用旧接口，一次拿全两阶段特征
template <typename T>
void probe_domain(Feat<T>& f) {
    probe_range(f);
    probe_dup(f);
}

// ==================================================================
// 三、策略层
// ==================================================================
enum class Strat {
    SmallBinaryInsert,
    SmallInsert,
    AlreadyAscending,
    ReverseToAscending,
    CountingSort,
    RadixLSD,
    Quick3Way,
    MergeSorted,
    IntroSort,
    Undefined
};

inline const char* strat_name(Strat s) {
    switch (s) {
        case Strat::SmallBinaryInsert:  return "S1 二分插入";
        case Strat::SmallInsert:        return "S1b 插入排序";
        case Strat::AlreadyAscending:   return "S2 已升序直通";
        case Strat::ReverseToAscending: return "S3 降序反转";
        case Strat::CountingSort:       return "S4 计数排序";
        case Strat::RadixLSD:           return "S5 基数排序";
        case Strat::Quick3Way:          return "S6 三路快排";
        case Strat::MergeSorted:        return "S7 边界检测归并";
        case Strat::IntroSort:          return "S8 内省快排";
        default:                        return "??";
    }
}

// 记录最近一次走的分支，供报告核对
inline Strat& last_strategy() { static Strat s = Strat::Undefined; return s; }

// 最近一次基数策略实际选用的数字位宽（8/16；0 = 本次未走基数）。
// 仅用于决策追踪（trace/驱动展示），不参与任何控制流。
inline int& last_radix_db() { static int db = 0; return db; }

// ---- S1：二分插入排序 ----
template <typename T>
void binary_insertion_sort(T* a, int n) {
    for (int i = 1; i < n; ++i) {
        T key = a[i]; tick_mv();
        int lo = 0, hi = i;
        while (lo < hi) {
            int mid = (lo + hi) >> 1;
            if (lt(key, a[mid])) hi = mid; else lo = mid + 1;
        }
        for (int j = i; j > lo; --j) mv(a[j], a[j - 1]);
        if (lo != i) mv(a[lo], key);
    }
}

// ---- S2/S3：全量单调性确认与反转 ----
template <typename T>
inline bool verify_ascending(const T* a, int n) {
    for (int i = 1; i < n; ++i) { tick_cmp(); if (a[i] < a[i - 1]) return false; }
    return true;
}

template <typename T>
inline bool verify_descending(const T* a, int n) {
    for (int i = 1; i < n; ++i) { tick_cmp(); if (a[i - 1] < a[i]) return false; }
    return true;
}

template <typename T>
inline void reverse_array(T* a, int n) {
    for (int i = 0, j = n - 1; i < j; ++i, --j) swp(a[i], a[j]);
}

// ---- S4：计数排序（整数键，值域 [lo, hi] 已知） ----
template <typename T>
void counting_sort_range(T* a, int n, long long lo, long long hi) {
    const std::size_t k = std::size_t(hi - lo + 1);
    Buf<unsigned int> cntb(k, true);
    unsigned int* cnt = cntb.data();
    for (int i = 0; i < n; ++i) { tick_cmp(); ++cnt[std::size_t((long long)a[i] - lo)]; }
    unsigned int sum = 0;
    for (std::size_t i = 0; i < k; ++i) { unsigned int t = cnt[i]; cnt[i] = sum; sum += t; }
    Buf<T> out(n);
    T* o = out.data();
    for (int i = 0; i < n; ++i) {
        std::size_t p = std::size_t((long long)a[i] - lo);
        mv(o[cnt[p]++], a[i]);
    }
    for (int i = 0; i < n; ++i) mv(a[i], o[i]);
}

// 有效位宽：to_ukey(hi) ^ to_ukey(lo) 的最高非零位（只跑必要的基数趟数）
template <typename T>
inline int significant_bits(T lo, T hi) {
    using U = std::make_unsigned_t<T>;
    U x = U(to_ukey(hi) ^ to_ukey(lo));
    int b = 0;
    while (x) { ++b; x >>= 1; }
    return b < 1 ? 1 : b;
}

// ==================================================================
// 四、成本模型自标定
//    做法：在真实工作负载上实测各策略耗时，再拟合模型系数。
//    标定一次约 60~80 ms，只发生在进程启动阶段，不计入任何计时区间。
// ==================================================================
namespace calib {

struct Rng {
    std::uint64_t s;
    explicit Rng(std::uint64_t seed) : s(seed ? seed : 1) {}
    inline std::uint32_t u32() {
        s ^= s >> 12; s ^= s << 25; s ^= s >> 27;
        return std::uint32_t((s * 2685821657736338717ull) >> 32);
    }
    inline int uni(int lo, int hi) { return lo + int(u32() % std::uint32_t(hi - lo + 1)); }
};

template <typename T, typename F>
inline double best_ms(const std::vector<T>& base, F fn, int reps) {
    double best = 1e300;
    for (int r = 0; r < reps; ++r) {
        std::vector<T> w(base);
        double t0 = now_ns();
        fn(w.data(), int(w.size()));
        double t1 = now_ns();
        g_sink += (double)w[w.size() / 2];
        double ms = (t1 - t0) / 1e6;
        if (ms < best) best = ms;
    }
    return best;
}

}  // namespace calib

// v3.1：标定按键类型 T 实例化。int64 posting 键（第 8 点应用）的访存量是
// int 的两倍，比较类/基数类的真实单价随之不同 —— 用 int 标定的系数给
// int64 决策会造成系统性偏差，故标定负载与决策目标同类型。
template <typename T>
inline CostModel calibrate_cost_model_T() {
    CountScope cs(false);   // 标定过程不计数
    CostModel m;

    const int KV = 1000000;   // 大值域 = 最高 k 锚点（CostModel::KANC[4]）

    // 7 个 log 等距规模（每档 ×√10）。选点依据有两条：
    //  (1) 必须覆盖 L1→L2→L3 的 cache 台阶。计数排序的"每元素成本"在
    //      n≈4096（16 KB，L1 驻留）与 n≈10^4 之间会跳一个台阶；早期版本只标
    //      4096/65536/262144/1048576 四个点，用幂律插值跨越台阶，把 n=10^4 的
    //      每元素成本低估了 3.3 倍，于是"计数排序 vs 归并排序"判反。
    //  (2) 取点与实验用的规模（10^3、10^4、10^5、10^6）对齐，使决策点上的
    //      系数是实测而非插值 —— 自适应排序的全部意义就是在本机实测标定，
    //      没有理由让最关键的那几档去承担插值误差。
    struct Sz { int n; int reps; };
    // reps 档位（v3 加档）：标定系数的信噪比直接决定边界决策的稳定性。
    // 大规模档单次计时接近时钟量化与背景噪声，轮数不足以取到稳定下界 —— 旧档
    // 在 n=10^6 只测 1 轮。加档后标定总耗时约 0.5s→0.8s，仍不进入任何计时区间。
    // v3.1 再加 n=100 档（np 恰为 MAXP=8）：实验矩阵的最小规模是 10^2，旧档
    // 从 10^3 起步，使 n=100 的全部系数靠"钳位到 n=1000"外推 —— 对基数排序
    // 这类"每趟含固定桶表开销"的策略，该外推会把小 n 的单趟成本高估数倍
    // （桶表 memset 占比随 n 缩小而膨胀），直接造成 8 位/16 位竞争在 n=100
    // 判反。决策点上的系数必须实测，故补齐该档。
    const Sz sizes[] = {{100, 8}, {1000, 5}, {3162, 4}, {10000, 4}, {31623, 3},
                        {100000, 3}, {316228, 2}, {1000000, 2}};

    for (const Sz& s : sizes) {
        const int n = s.n;
        std::vector<T> A(n), D(n), S(n), B(n), E(n), F(n), G(n);
        calib::Rng rng(0x9E3779B97F4A7C15ull);
        for (int i = 0; i < n; ++i) A[i] = (T)rng.uni(1, KV);          // 完全无序（值域 = 最高 k 锚点）
        for (int i = 0; i < n; ++i) D[i] = (T)rng.uni(0, int(CostModel::KANC[1]) - 1);  // 高重复率（锚点 1）
        for (int i = 0; i < n; ++i) S[i] = (T)i;                       // 近序：离散扰动
        for (int t = 0; t < n / 50; ++t) { std::swap(S[rng.uni(0, n - 2)], S[rng.uni(0, n - 2) + 1]); }
        // 近序：块状扰动（前 90% 已排序 + 后 10% 随机）。
        // 与 S 并列而不是取代 S，是因为两者的归并成本差 3.7 倍，
        // 而"逆序率"这个统计量分不开它们（实测都是 0.037），
        // 只能各标一套系数，运行期用 asc_prefix 选。
        for (int i = 0; i < n; ++i) B[i] = (i < n - n / 10) ? (T)i : (T)rng.uni(0, n - 1);
        // k 标定网格的 5 个值域负载（与 CostModel::KANC 一一对应）
        for (int i = 0; i < n; ++i) E[i] = (T)rng.uni(0, int(CostModel::KANC[0]) - 1);
        for (int i = 0; i < n; ++i) F[i] = (T)rng.uni(0, int(CostModel::KANC[2]) - 1);
        for (int i = 0; i < n; ++i) G[i] = (T)rng.uni(0, int(CostModel::KANC[3]) - 1);

        CostModel::Pt p{};
        p.n = double(n);

        double t_intro = calib::best_ms(A, [](T* a, int k) { intro_sort(a, k); }, s.reps);
        p.v[CF_CMP_GEN] = t_intro / (double(n) * std::log2(double(n)));

        double t_q3 = calib::best_ms(D, [](T* a, int k) { quick_sort_3way(a, k); }, s.reps);
        p.v[CF_CMP_DUP] = t_q3 / (double(n) * std::log2(double(n)));

        const double nl = double(n) * std::log2(double(n));
        double t_ms = calib::best_ms(S, [](T* a, int k) { merge_sort(a, k); }, s.reps);
        p.v[CF_MSORT] = t_ms / nl;

        // 块状近序：归并 + 内省快排各一套系数
        double t_msb = calib::best_ms(B, [](T* a, int k) { merge_sort(a, k); }, s.reps);
        p.v[CF_MSORT_BLK] = t_msb / nl;
        double t_inb = calib::best_ms(B, [](T* a, int k) { intro_sort(a, k); }, s.reps);
        p.v[CF_CMP_BLK] = t_inb / nl;

        // 离散近序的比较类成本（内省快排跑 S）
        double t_ins = calib::best_ms(S, [](T* a, int k) { intro_sort(a, k); }, s.reps);
        p.v[CF_CMP_NEAR] = t_ins / nl;

        // 计数排序：(n,k) 标定网格的 k 维 —— 5 个锚点直接实测耗时（v3）。
        // D 承担锚点 1（k=1024），A 承担锚点 4（k=10^6），E/F/G 为新增负载。
        // 锚点直接存实测值，不再做 a·n+b·k 参数分解（理由见 est_count 处注释）。
        p.v[CF_CNT_K0] = calib::best_ms(E, [](T* a, int k) { counting_sort_range(a, k, 0, CostModel::KANC[0] - 1); }, s.reps);
        p.v[CF_CNT_K1] = calib::best_ms(D, [](T* a, int k) { counting_sort_range(a, k, 0, CostModel::KANC[1] - 1); }, s.reps);
        p.v[CF_CNT_K2] = calib::best_ms(F, [](T* a, int k) { counting_sort_range(a, k, 0, CostModel::KANC[2] - 1); }, s.reps);
        p.v[CF_CNT_K3] = calib::best_ms(G, [](T* a, int k) { counting_sort_range(a, k, 0, CostModel::KANC[3] - 1); }, s.reps);
        p.v[CF_CNT_K4] = calib::best_ms(A, [](T* a, int k) { counting_sort_range(a, k, 1, CostModel::KANC[4]); }, s.reps);

        // 基数排序单趟耗时（16 位：2 趟 ÷ 2）
        double t_rad = calib::best_ms(A, [](T* a, int k) { radix_sort_lsd<T>(a, k, sizeof(T) * 8); }, s.reps);
        p.v[CF_RAD_P] = t_rad / (sizeof(T) >= 8 ? 4.0 : 2.0);

        // 基数排序单趟耗时（8 位：4 趟 ÷ 4）—— v3.1 新增。
        // 主文档 §11.6 的实测显示位宽优劣随 n 与分布反转（DB=8 在 n=10^4 快
        // 27%~34%、在泊松 n=10^5 慢 1.6 倍），不存在固定最优位宽；正确做法是
        // 两个位宽各标一套单趟成本，决策期按 趟数 × 单趟 竞争。此为实现。
        double t_rad8 = calib::best_ms(A, [](T* a, int k) { radix_sort_lsd<T, 8>(a, k, sizeof(T) * 8); }, s.reps);
        p.v[CF_RAD8_P] = t_rad8 / (sizeof(T) >= 8 ? 8.0 : 4.0);

        // 兜底：任何异常值退回保守默认
        for (int i = 0; i < CF_COUNT; ++i)
            if (!(p.v[i] > 0) || !std::isfinite(p.v[i])) p.v[i] = 1e-6;

        m.pt[m.np++] = p;
    }
    return m;
}

template <typename T>
inline const CostModel& cost_model_for() {
    static CostModel m = calibrate_cost_model_T<T>();   // 按 T 各自标定（magic static）
    return m;
}
inline const CostModel& cost_model() { return cost_model_for<int>(); }

// ==================================================================
// 五、决策层 + 主入口
// ==================================================================
template <typename T>
void adapt_sort_impl(T* a, int n, const CostModel& cm) {
    last_radix_db() = 0;   // 每次决策前清空位宽记录（仅追踪用）
    if (n < 2) { last_strategy() = Strat::SmallBinaryInsert; return; }

    // ---------- 规则 1：极小规模直接二分插入 ----------
    if (n <= 32) {
        last_strategy() = Strat::SmallBinaryInsert;
        binary_insertion_sort(a, n);
        return;
    }

    // ---------- 特征探测阶段 A：顺序结构（O(√n)，每采样点 2 次比较） ----------
    Feat<T> f = probe_order(a, n);

    // ---------- 规则 2：整体单调性（采样一致 → 全量确认） ----------
    // 正序：n-1 次比较即可判定，这已是信息论下界 —— 任何算法都必须至少比较 n-1 次
    //       才能确认有序，否则存在未比较的相邻对可能逆序。
    // 逆序：n-1 次比较确认 + n/2 次交换反转，同为 O(n) 下界（每个元素必须被搬动）。
    if (f.asc_sample && verify_ascending(a, n)) {
        last_strategy() = Strat::AlreadyAscending;
        return;
    }
    if (f.desc_sample && verify_descending(a, n)) {
        last_strategy() = Strat::ReverseToAscending;
        reverse_array(a, n);
        return;
    }

    // ---------- 规则 2.5：小规模 + 近似有序 → 直接插入排序 ----------
    // n 小的时候"决策开销"与"排序本身"是同一量级（n=100 时一次排序仅约 1 us），
    // 此时最优解是"最便宜的探测 + 最便宜的策略"：采样已经给出了低逆序率，
    // 直接插入排序在近似有序数据上是 O(n + inv)，且没有任何额外常数。
    // 这一步专门覆盖真实 CVE 语料在小规模档的形态（高重复 + 近似有序）。
    if (n <= 512 && f.inv_rate <= 0.05) {
        last_strategy() = Strat::SmallInsert;
        insertion_sort(a, n);
        return;
    }

    // ---------- 规则 3~5：整数键 → 值域类策略参与竞争 ----------
    if constexpr (std::is_integral_v<T>) {
        probe_range(f);           // 值域先行（2(dn-1) 次比较）——规则 2.6 与值域分析都依赖它
        // ---------- 规则 2.6：小规模 + 窄值域 → 计数排序（确定性规则） ----------
        // n ≤ 512 时，逐进程标定的系数噪声与排序成本同量级（n=100 档一次排序
        // 约 0.5μs，且该规模低于最小标定档、模型靠外推），模型竞争在此是
        // "用噪声做决策"——泊松 n=100 曾因此在计数/内省之间跑间翻转。
        // 而窄值域下计数的优势由 cache 几何单方面决定（3 趟顺序访问 vs 快排的
        // 分支划分，实测 ~1.6×），不依赖任何系数：采样值域 ≤ 2n 即触发，
        // 精确值域由 2n 全量扫描给出；若真实 k 意外地大（采样恰好聚簇），
        // 扫描后回退内省快排——扫描成本 2n 在该规模约 0.2μs，可接受。
        if (n <= 512 && f.sample_range + 1 <= 2 * (long long)n) {
            T mn = a[0], mx = a[0];
            for (int i = 1; i < n; ++i) {
                tick_cmp(); if (a[i] < mn) mn = a[i];
                tick_cmp(); if (mx < a[i]) mx = a[i];
            }
            const long long k26 = (long long)mx - (long long)mn + 1;
            if (k26 <= 8LL * n) {   // 计数排序的额外工作 ≈ k，超过 ~8n 则不再稳赢
                last_strategy() = Strat::CountingSort;
                counting_sort_range(a, n, (long long)mn, (long long)mx);
            } else {
                last_strategy() = Strat::IntroSort;
                intro_sort(a, n);
            }
            return;
        }
        const int passes_full = (int(sizeof(T) * 8) + 15) / 16;
        cm.prepare(double(n), passes_full, f.m);   // 系数与每规模派生量一并缓存（改进②）
        const double nl = cm.nl();
        const double e_radix_full = cm.e_radix_full();

        // 是否值得花 2n 次比较去精确求值域（min/max 全量扫描）？
        //
        // 这一判断有两处必须做对，否则扫描会白扫：
        //
        // (1) 采样值域是真实值域的**下界**，直接拿它当 k 会系统性低估计数排序成本。
        //     极值统计给出修正：m 个样本的极差约为 2σ√(2 ln m)，n 个元素的极差约为
        //     2σ√(2 ln n)，故真实极差约为采样极差的 √(ln n / ln m) 倍
        //     （m=128、n=10^6 时约 1.7 倍）。不做修正时，高斯分布在 n=10^6 档
        //     采样值域 3.6×10^5 让计数排序看着只要 9.1 ms（低于基数的 10.9 ms），
        //     于是白扫 2n 次比较；而真实值域 6.3×10^5 使计数排序实际要 12.0 ms，
        //     决策根本没变，那 2n 次比较（约占该档总耗时 18%）纯属浪费。
        //
        // (2) 扫描本身有代价（2n 次顺序比较），收益必须超过代价才做。
        //     收益有两条来源，取大者：
        //       a. 把基数排序的趟数从全宽降到实际位宽 —— 只有采样位宽已经小于全宽
        //          时才可能（采样值域是下界 ⇒ 真实位宽只会更大，不会更小）；
        //       b. 让计数排序成为可选项，收益上界 = 基数成本 - 计数成本（用修正后的 k）。
        const long long k_est = (long long)(double(f.sample_range + 1) * cm.infl());
        const double e_count_est = cm.est_count(n, k_est);
        const double scan_cost = cm.scan_cost();
        const int bits_lb = significant_bits<T>(T(f.smin), T(f.smax));
        const int passes_lb = (bits_lb + 15) / 16;
        const double gain_radix = double(passes_full - passes_lb) * cm.coef(CF_RAD_P);
        const double gain_count = std::max(0.0, e_radix_full - e_count_est);
        const bool scan_worth = (std::max(gain_radix, gain_count) > scan_cost * 1.30);

        long long lo = (long long)f.smin, hi = (long long)f.smax;
        bool exact = false;
        if (scan_worth) {
            T mn = a[0], mx = a[0];
            for (int i = 1; i < n; ++i) {
                tick_cmp(); if (a[i] < mn) mn = a[i];
                tick_cmp(); if (mx < a[i]) mx = a[i];
            }
            lo = (long long)mn; hi = (long long)mx; exact = true;
        }
        const long long k = hi - lo + 1;
        const int bits = exact ? significant_bits<T>(T(lo), T(hi)) : int(sizeof(T) * 8);
        const int passes = (bits + 15) / 16;

        // 比较类候选预测：此刻 dup_rate 尚未测（延迟付费，改进②）。按"非高重复"
        // 系数预测：对真正高重复的输入是高估，偏差方向是把决策推向值域类——
        // 而值域类（计数/基数）对重复键天然免疫，故该偏差方向安全。近序但高重复
        // 的输入先按归并预测，落入比较类后再补测 dup 改选三路，两条路径都能
        // 正确处理重复键。
        // 近序但"扰动成块"（如已排序前缀 + 随机尾段）时，归并只在前缀与尾段
        // 的交界及其后做实际搬移，成本远低于离散扰动；用 asc_prefix 区分。
        const bool blk = (f.asc_prefix >= 0.75);
        Strat cmp_strat;
        double e_cmp;
        if (f.inv_rate <= 0.05) { cmp_strat = Strat::MergeSorted; e_cmp = cm.est_msort_near(nl, blk); }
        else                    { cmp_strat = Strat::IntroSort;   e_cmp = cm.est_cmp(n, nl, false); }

        const double e_count = exact ? cm.est_count(n, k) : 1e300;

        // v3.1：基数排序的数字位宽参与竞争。DB=16 趟数少但每趟要跨 256KB 桶表
        //（只能驻 L2）；DB=8 趟数多但桶表 1KB 恒驻 L1。优劣随 n 与位宽反转
        // （§11.6 实测），故两个位宽各按本机标定的单趟成本估一遍，取最小者。
        const int passes8 = (bits + 7) / 8;
        const double e_radix16 = cm.est_radix(n, passes, 16);
        const double e_radix8 = cm.est_radix(n, passes8, 8);
        const int radix_db = (e_radix8 <= e_radix16) ? 8 : 16;
        const double e_radix = std::min(e_radix16, e_radix8);

        // 值域类 vs 比较类的切换裕度按"模型可信度"分设（v3.1）：
        //   * 计数排序 1.30 —— 其成本要在 (n,k) 两个维度上外推（标定网格外的
        //     k 靠插值/外推），实测外推误差可达 40%，模型最不可信；
        //   * 基数排序 1.10 —— 单趟系数在同类负载上直接实测，趟数由位宽
        //     确定性导出，可信度与比较类系数同级，只留 10% 噪声裕度。
        // 旧版对两者统一用 1.30，使基数在"预测略胜但不足 30%"的小规模格
        // （均匀/高斯 n=10²，绝对差 ~1μs）被过度保守地留在比较类。
        const double MARGIN = 1.30;    // 计数排序进入比较
        const double RMARGIN = 1.10;   // 基数排序进入比较
        // 计数 vs 基数的不对称裕度（改进①）：即使 (n,k) 网格标定后，计数模型的
        // 插值误差仍天然大于"趟数 × 同形态负载实测单趟"的基数模型（前者跨
        // cache 台阶插值、后者是同形态直接实测）；要求 15% 优势才选计数。
        // 前提：计数模型必须足够可信 —— 旧两点解模型下泊松 n=10^6 格裕度仅
        // 1.077，直接加它会误翻成基数；网格标定后该格裕度约 1.5。
        const double CMARGIN = 1.15;
        if ((e_count * MARGIN <= e_cmp) || (e_radix * RMARGIN <= e_cmp)) {
            // 计数被选中须同时满足：预测优于基数×CMARGIN，且自身总体裕度过关
            if (e_count * CMARGIN <= e_radix && e_count * MARGIN <= e_cmp) {
                last_strategy() = Strat::CountingSort;
                counting_sort_range(a, n, lo, hi);
            } else {
                last_strategy() = Strat::RadixLSD;
                last_radix_db() = radix_db;
                if (radix_db == 8) radix_sort_lsd<T, 8>(a, n, bits);
                else               radix_sort_lsd<T, 16>(a, n, bits);
            }
            return;
        }
        // 落入比较类：此刻才需要 dup_rate。n ≤ 512 连 dup 也不测：该规模下二路
        // Hoare 划分对重复键天然平衡（全等输入对半分），三路收益低于探测成本。
        if (n > 512) probe_dup(f);
        if (n > 512 && f.dup_rate >= 0.20) cmp_strat = Strat::Quick3Way;
        last_strategy() = cmp_strat;
        switch (cmp_strat) {
            case Strat::Quick3Way:   quick_sort_3way(a, n); break;
            case Strat::MergeSorted: merge_sort(a, n);      break;
            default:                 intro_sort(a, n);      break;
        }
        return;
    }

    // ---------- 规则 6：通用键（字符串等）→ 只能走比较类家族 ----------
    if (n > 512) probe_dup(f);      // 只有比较类可选时才需要 dup（值域用不上，顺带省掉 min/max）
    Strat s = Strat::IntroSort;
    if (f.dup_rate >= 0.20) s = Strat::Quick3Way;
    else if (f.inv_rate <= 0.05) s = Strat::MergeSorted;
    last_strategy() = s;
    switch (s) {
        case Strat::Quick3Way:   quick_sort_3way(a, n); break;
        case Strat::MergeSorted: merge_sort(a, n);      break;
        default:                 intro_sort(a, n);      break;
    }
}

// 对外统一入口：升序
template <typename T>
void adapt_sort(T* a, int n) {
    adapt_sort_impl(a, n, cost_model_for<T>());
}

// 对外统一入口：降序（= 升序 + O(n) 反转；降序排序的下界与升序同为 Ω(n log n)/Ω(n)，
// 故该实现同样渐近最优，且复用了全部自适应分支）
template <typename T>
void adapt_sort_desc(T* a, int n) {
    adapt_sort_impl(a, n, cost_model_for<T>());
    reverse_array(a, n);
}

}  // namespace sb
