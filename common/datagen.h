/* datagen.h —— 实验内容 2/6：关键字序列生成器
 *
 * 双语核心：同一份源码分别被 C / C++ 构建使用，且随机源、种子语义完全一致，
 * 因此两个语言版本在每个 (分布, 规模) 单元上生成**逐字节相同**的数据 ——
 * 这是 C vs C++ 对比实验成立的前提（同时使"比较次数必须完全一致"成为
 * 交叉验证两个构建正确性的强断言，由 tools/compare_c_cpp.py 检查）。
 *
 * 分布语义与 v1（legacy/）完全一致，保证与旧结果可比：
 *   均匀    —— [0, RANGE) 均匀整数
 *   高斯    —— Box-Muller，mu=RANGE/2, sigma=RANGE/6，截断到 [0, RANGE)
 *   泊松    —— Knuth 算法，lambda=50
 *   正序    —— 1..n；逆序 —— n..1
 * 新增第 6 类"CVE替身"（服务评分点 7/8 的真实数据叙事，非课程硬性要求）：
 *   按 proposal 1.2 节实测统计量构造的统计等价序列 —— 39 个离散键
 *   （5 档 severity + 34 档 CVSS 合并编码）、前 92.5% 位置非降（2025+2026
 *   档案占比）、尾部乱序、键内少量相邻交换。拿到真实 CVE 语料后应以真实
 *   解析结果替换本生成器。
 */
#ifndef DATAGEN_H
#define DATAGEN_H

#include <math.h>
#include "seqlist.h"

#define RANGE 1000000   /* 关键字取值范围 [0, RANGE) */

typedef enum DistType {
    DIST_RANDOM_UNIFORM = 0,
    DIST_GAUSSIAN,
    DIST_POISSON,
    DIST_SORTED_ASC,
    DIST_SORTED_DESC,
    DIST_CVE,           /* 扩展：真实 CVE 派生（统计等价替身） */
    DIST_COUNT
} DistType;

/* CSV/机器可读的分布 id */
static const char *distId(DistType d) {
    switch (d) {
        case DIST_RANDOM_UNIFORM: return "uniform";
        case DIST_GAUSSIAN:       return "gauss";
        case DIST_POISSON:        return "poisson";
        case DIST_SORTED_ASC:     return "asc";
        case DIST_SORTED_DESC:    return "desc";
        case DIST_CVE:            return "cve";
        default:                  return "?";
    }
}

/* 中文显示名 */
static const char *distName(DistType d) {
    switch (d) {
        case DIST_RANDOM_UNIFORM: return "均匀分布随机";
        case DIST_GAUSSIAN:       return "高斯分布";
        case DIST_POISSON:        return "泊松分布";
        case DIST_SORTED_ASC:     return "正序";
        case DIST_SORTED_DESC:    return "逆序";
        case DIST_CVE:            return "CVE替身";
        default:                  return "未知";
    }
}

/* ---------------- 自带 32 位随机源（xorshift32） ----------------
 * 不依赖平台 CRT 的 rand()（Windows MinGW 仅 15 位，无法覆盖 [0,1e6)，
 * 且跨语言/跨平台行为不一致）。固定种子可复现。 */
static unsigned int g_drng = 1u;

static unsigned int drng(void) {
    unsigned int x = g_drng;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return g_drng = x;
}
/* [0,1) 的 double */
static double drng01(void) {
    return (double)(drng() + 1u) / 4294967296.0;
}

/* ---------------- 各分布生成 ---------------- */

static void genUniform(int *a, int n) {
    for (int i = 0; i < n; i++)
        a[i] = (int)(drng01() * RANGE);
}

static void genGaussian(int *a, int n) {
    const double mu = RANGE / 2.0;
    const double sigma = RANGE / 6.0;
    for (int i = 0; i < n; i++) {
        double u1 = drng01();
        double u2 = drng01();
        double z = sqrt(-2.0 * log(u1)) * cos(6.28318530718 * u2);
        int v = (int)(mu + sigma * z);
        if (v < 0) v = 0;
        if (v >= RANGE) v = RANGE - 1;
        a[i] = v;
    }
}

static int poisson_lambda50(void) {
    const double L = exp(-50.0);
    int k = 0;
    double p = 1.0;
    do {
        k++;
        p *= drng01();
    } while (p > L);
    return k - 1;
}
static void genPoisson(int *a, int n) {
    for (int i = 0; i < n; i++)
        a[i] = poisson_lambda50();
}

static void genSortedAsc(int *a, int n) {
    for (int i = 0; i < n; i++) a[i] = i + 1;
}

static void genSortedDesc(int *a, int n) {
    for (int i = 0; i < n; i++) a[i] = n - i;
}

/* CVE 替身：39 个离散键，前 92.5% 非降，尾部乱序，键内 n/50 次相邻交换 */
static void genCVE(int *a, int n) {
    const int LEVELS = 39;
    const int sorted_part = (int)((double)n * 0.925);
    for (int i = 0; i < sorted_part; ++i)
        a[i] = (int)((unsigned long long)i * (unsigned)LEVELS /
                     (unsigned)(sorted_part ? sorted_part : 1));
    for (int i = sorted_part; i < n; ++i)
        a[i] = (int)(drng() % (unsigned)LEVELS);
    /* 同键内部轻微乱序：模拟同 severity/CVSS 档内日期无序 */
    const int swaps = n / 50;
    for (int s = 0; s < swaps; ++s) {
        int i = (int)(drng() % (unsigned)(n > 1 ? n - 1 : 1));
        int t = a[i]; a[i] = a[i + 1]; a[i + 1] = t;
    }
}

/* 统一入口：生成 n 个关键字到顺序表 L（不足则扩容），种子可复现 */
static void generateData(SeqList *L, int n, DistType dist, unsigned int seed) {
    if (!L || !L->data || n <= 0) return;
    if (n > L->capacity) SeqList_Resize(L, n);
    g_drng = seed ? seed : 1u;
    int *a = L->data;
    switch (dist) {
        case DIST_RANDOM_UNIFORM: genUniform(a, n);   break;
        case DIST_GAUSSIAN:       genGaussian(a, n);  break;
        case DIST_POISSON:        genPoisson(a, n);   break;
        case DIST_SORTED_ASC:     genSortedAsc(a, n); break;
        case DIST_SORTED_DESC:    genSortedDesc(a, n);break;
        case DIST_CVE:            genCVE(a, n);       break;
        default: break;
    }
    L->length = n;
}

/* 直接生成到调用方数组（基准框架用，免去 SeqList 语义） */
static void generateArray(int *a, int n, DistType dist, unsigned int seed) {
    g_drng = seed ? seed : 1u;
    switch (dist) {
        case DIST_RANDOM_UNIFORM: genUniform(a, n);   break;
        case DIST_GAUSSIAN:       genGaussian(a, n);  break;
        case DIST_POISSON:        genPoisson(a, n);   break;
        case DIST_SORTED_ASC:     genSortedAsc(a, n); break;
        case DIST_SORTED_DESC:    genSortedDesc(a, n);break;
        case DIST_CVE:            genCVE(a, n);       break;
        default: break;
    }
}

#endif /* DATAGEN_H */
