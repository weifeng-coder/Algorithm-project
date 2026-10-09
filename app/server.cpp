/* server.cpp —— CineRank 应用主程序（评分点 8/9/10）
 *
 * 单二进制五种模式：
 *   selftest  正确性自检（int64 合成数据 + 真实数据，与 std::sort 逐元素比对）
 *   matrix    真实数据排序矩阵：postings 各档 × (8 自实现 + AdaptSort + std::sort)
 *             → results/results_real.csv（评分点 8 的核心证据）
 *   extsort   外部排序（BSBI）：内存 1/10 限流，块内 AdaptSort + k 路堆归并，
 *             统计 I/O 块数（评分点 10 扩充一）
 *   topk      Top-K 选择对比：堆 O(n log k) vs 快速选择 O(n) vs 全排序
 *             （评分点 10 扩充二）
 *   server    HTTP 服务（评分点 9 人机交互界面）：WinSock2 手写最小服务器 +
 *             浏览器前端（app/frontend/index.html，ECharts 本地化）
 *
 * 编译见 build_app.bat。
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>
#include <string>
#include <vector>
#include <array>
#include <map>
#include <algorithm>
#include <fstream>
#include <sstream>
#include <chrono>
#include <dirent.h>
#include <sys/stat.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#endif

#include "sortstats.h"
#include "datagen.h"
#include "sortcore.h"
#include "bench.h"

#include "sortgen.h"
#include "ml_index.h"
#include "entity_index.h"
#include "webgraph.h"
#include "sufarray.h"
#include "adaptsort.h"

using namespace std;
using namespace sb;

static string g_datadir = "app/data";
static string g_wwwdir = "app/frontend";
static string g_resdir = "results";

/* ================================================================
 * 一、动画帧记录器（评分点 9：排序过程可视化，n ≤ 64）
 * 每帧 = 数组快照 + (比较次数, 移动次数, 最近触达下标 i/j)。
 * 与被测排序内核分离：动画实现为教学版（确定性枢轴、无批量优化）。
 * ================================================================ */
struct Frames {
    int n = 0;
    long long calls = 0;          /* push 调用总数 = 可视化事件数 */
    long long keepEvery = 1;      /* 抽帧步长：容量满后逐次翻倍 */
    int budget = 720;             /* 最大保留帧数（大 n 由调用方按 1.6M/n 调低） */
    vector<vector<int>> arrs;
    vector<array<int, 4>> meta;   /* cmp, mv, i, j */
    string strategy;              /* AdaptSort 的决策标签（其余算法为空） */
    void push(const vector<int>& a, long long c, long long m, int i, int j) {
        calls++;
        bool finalFrame = (i == -1 && j == -1);   /* 收尾帧（有序终态）永远保留 */
        if (!finalFrame && (calls % keepEvery) != 0) return;
        if ((int)arrs.size() >= budget) {
            /* 容量满：隔一抽一（首帧必留）→ 帧数减半、步长翻倍，全程覆盖均匀 */
            size_t w = 1;
            for (size_t k = 2; k < arrs.size(); k += 2) {
                arrs[w] = std::move(arrs[k]);
                meta[w] = meta[k];
                ++w;
            }
            arrs.resize(w);
            meta.resize(w);
            keepEvery *= 2;
        }
        arrs.push_back(a);
        meta.push_back({(int)c, (int)m, i, j});
    }
};

typedef void (*AnimFn)(vector<int> a, Frames& F);

static void anim_bubble(vector<int> a, Frames& F) {
    long long c = 0, m = 0; int n = (int)a.size();
    auto cmp = [&](int x, int y) { c++; return (x > y) - (x < y); };
    for (int i = 0; i < n - 1; i++) {
        bool sw = false;
        for (int j = 0; j < n - 1 - i; j++) {
            if (cmp(a[j], a[j + 1]) > 0) { swap(a[j], a[j + 1]); m += 3; sw = true; F.push(a, c, m, j, j + 1); }
        }
        if (!sw) break;
    }
    F.push(a, c, m, -1, -1);
}

static void anim_insertion(vector<int> a, Frames& F) {
    long long c = 0, m = 0; int n = (int)a.size();
    auto cmp = [&](int x, int y) { c++; return (x > y) - (x < y); };
    for (int i = 1; i < n; i++) {
        int key = a[i]; m++;
        int j = i - 1;
        while (j >= 0 && cmp(a[j], key) > 0) { a[j + 1] = a[j]; m++; F.push(a, c, m, j, j + 1); j--; }
        a[j + 1] = key; m++; F.push(a, c, m, i, j + 1);
    }
    F.push(a, c, m, -1, -1);
}

static void anim_selection(vector<int> a, Frames& F) {
    long long c = 0, m = 0; int n = (int)a.size();
    auto cmp = [&](int x, int y) { c++; return (x > y) - (x < y); };
    for (int i = 0; i < n - 1; i++) {
        int sel = i;
        for (int j = i + 1; j < n; j++) if (cmp(a[j], a[sel]) < 0) sel = j;
        if (sel != i) { swap(a[i], a[sel]); m += 3; F.push(a, c, m, i, sel); }
    }
    F.push(a, c, m, -1, -1);
}

static void anim_shell(vector<int> a, Frames& F) {
    long long c = 0, m = 0; int n = (int)a.size();
    auto cmp = [&](int x, int y) { c++; return (x > y) - (x < y); };
    for (int gap = n / 2; gap > 0; gap /= 2)
        for (int i = gap; i < n; i++) {
            int key = a[i]; m++;
            int j = i - gap;
            while (j >= 0 && cmp(a[j], key) > 0) { a[j + gap] = a[j]; m++; F.push(a, c, m, j, j + gap); j -= gap; }
            a[j + gap] = key; m++; F.push(a, c, m, i, j + gap);
        }
    F.push(a, c, m, -1, -1);
}

static void anim_quick_rec(vector<int>& a, int l, int r, long long& c, long long& m, Frames& F) {
    auto cmp = [&](int x, int y) { c++; return (x > y) - (x < y); };
    if (l >= r) return;
    int pivot = a[(l + r) / 2];   /* 确定性枢轴（动画版取中点） */
    int i = l, j = r;
    while (i <= j) {
        while (cmp(a[i], pivot) < 0) i++;
        while (cmp(a[j], pivot) > 0) j--;
        if (i <= j) {
            if (i != j) { swap(a[i], a[j]); m += 3; F.push(a, c, m, i, j); }
            i++; j--;
        }
    }
    anim_quick_rec(a, l, j, c, m, F);
    anim_quick_rec(a, i, r, c, m, F);
}
static void anim_quick(vector<int> a, Frames& F) {
    long long c = 0, m = 0;
    anim_quick_rec(a, 0, (int)a.size() - 1, c, m, F);
    F.push(a, c, m, -1, -1);
}

static void anim_heap(vector<int> a, Frames& F) {
    long long c = 0, m = 0; int n = (int)a.size();
    auto cmp = [&](int x, int y) { c++; return (x > y) - (x < y); };
    auto sift = [&](int start, int end) {
        int i = start, j = 2 * i + 1;
        int tmp = a[i]; m++;
        while (j <= end) {
            if (j + 1 <= end && cmp(a[j], a[j + 1]) < 0) j++;
            if (cmp(tmp, a[j]) >= 0) break;
            a[i] = a[j]; m++; F.push(a, c, m, i, j);
            i = j; j = 2 * i + 1;
        }
        a[i] = tmp; m++; F.push(a, c, m, i, i);
    };
    for (int i = n / 2 - 1; i >= 0; i--) sift(i, n - 1);
    for (int i = n - 1; i > 0; i--) { swap(a[0], a[i]); m += 3; F.push(a, c, m, 0, i); sift(0, i - 1); }
    F.push(a, c, m, -1, -1);
}

static void anim_merge_rec(vector<int>& a, vector<int>& t, int l, int r, long long& c, long long& m, Frames& F) {
    auto cmp = [&](int x, int y) { c++; return (x > y) - (x < y); };
    if (l >= r) return;
    int mid = l + (r - l) / 2;
    anim_merge_rec(a, t, l, mid, c, m, F);
    anim_merge_rec(a, t, mid + 1, r, c, m, F);
    /* 动画帧会把已归并前缀写回 a；保留本区间源快照，避免覆盖
     * 尚未读取的元素后继续从 a 读取，造成中间帧丢值。 */
    vector<int> src(a.begin() + l, a.begin() + r + 1);
    vector<int> view = a;
    int i = l, j = mid + 1, k = l;
    auto emit = [&](int iMark, int jMark) {
        /* 动画快照要同时展示已归并前缀和仍未处理的源元素，
         * 因此把未消费元素紧跟在前缀之后，保证每帧都是同一多重集合。 */
        for (int p = l; p <= r; p++) view[p] = src[p - l];
        for (int p = l; p < k; p++) view[p] = t[p];
        int out = k;
        for (int q = i; q <= mid; q++) view[out++] = src[q - l];
        for (int q = j; q <= r; q++) view[out++] = src[q - l];
        F.push(view, c, m, iMark, jMark);
    };
    while (i <= mid && j <= r) {
        if (cmp(src[i - l], src[j - l]) <= 0) t[k++] = src[i++ - l];
        else t[k++] = src[j++ - l];
        m++;
        for (int p = l; p < k; p++) a[p] = t[p];   /* 展示用：同步已归并前缀 */
        emit(i - 1, j - 1);
    }
    while (i <= mid) { t[k++] = src[i++ - l]; m++; for (int p = l; p < k; p++) a[p] = t[p]; emit(i - 1, -1); }
    while (j <= r)   { t[k++] = src[j++ - l]; m++; for (int p = l; p < k; p++) a[p] = t[p]; emit(-1, j - 1); }
}
static void anim_merge(vector<int> a, Frames& F) {
    long long c = 0, m = 0;
    vector<int> t(a.size());
    anim_merge_rec(a, t, 0, (int)a.size() - 1, c, m, F);
    F.push(a, c, m, -1, -1);
}

/* 分配类排序的教学视图：按源元素身份追踪当前位置（重复值也不混淆）。
 * 写入目标位时，把被占用的元素移到腾出的源位置；输出缓冲仍按算法
 * 正常写入。这样每帧保留全部元素，同时展示逐个确定的目标位置。
 * 这里的交换仅用于展示，不计入排序内核的移动次数。 */
struct PlacementView {
    vector<int> values, sourceAt, position;
    explicit PlacementView(const vector<int>& a)
        : values(a), sourceAt(a.size()), position(a.size()) {
        for (int i = 0; i < (int)a.size(); ++i) sourceAt[i] = position[i] = i;
    }
    int place(int source, int dst) {
        int from = position[source];
        int displaced = sourceAt[dst];
        swap(values[from], values[dst]);
        swap(sourceAt[from], sourceAt[dst]);
        position[source] = dst;
        position[displaced] = from;
        return from;
    }
};

static void anim_radix(vector<int> a, Frames& F) {
    long long c = 0, m = 0; int n = (int)a.size();
    vector<int> tmp(n);
    F.push(a, c, m, -1, -1);   /* 初始帧：未排序原貌（否则首帧已是第一趟结果） */
    for (int pass = 0; pass < 4; pass++) {
        PlacementView view(a);
        int cnt[256] = {0};
        for (int i = 0; i < n; i++) {
            unsigned key = (unsigned)a[i] >> (pass * 8);
            if (pass == 3) key ^= 0x80;  /* signed int: negatives precede positives */
            cnt[key & 255]++;
        }
        for (int k = 1; k < 256; k++) cnt[k] += cnt[k - 1];
        for (int i = n - 1; i >= 0; i--) {
            unsigned key = (unsigned)a[i] >> (pass * 8);
            if (pass == 3) key ^= 0x80;
            int dst = --cnt[key & 255];
            tmp[dst] = a[i]; m++;
            int from = view.place(i, dst);
            F.push(view.values, c, m, dst, from);
        }
        a = tmp;
        F.push(a, c, m, pass, -1);
    }
    F.push(a, c, m, -1, -1);
}

static void anim_counting(vector<int> a, Frames& F) {
    long long c = 0, m = 0; int n = (int)a.size();
    if (n <= 0) { F.push(a, 0, 0, -1, -1); return; }
    int mn = *min_element(a.begin(), a.end()), mx = *max_element(a.begin(), a.end());
    int k = mx - mn + 1;
    vector<int> cnt(k, 0);
    for (int i = 0; i < n; i++) cnt[a[i] - mn]++;
    /* 计数前缀和确定稳定输出中的源下标，再从左向右展示目标位。
     * 不能直接覆盖原数组，否则中间帧会重复/丢失尚未处理的元素。 */
    for (int v = 1; v < k; ++v) cnt[v] += cnt[v - 1];
    vector<int> sources(n);
    for (int i = n - 1; i >= 0; --i) sources[--cnt[a[i] - mn]] = i;
    PlacementView view(a);
    F.push(a, c, m, -1, -1);
    for (int idx = 0; idx < n; ++idx) {
        int from = view.place(sources[idx], idx);
        m++;
        F.push(view.values, c, m, idx, from);
    }
    F.push(view.values, c, m, -1, -1);
}

static void anim_adapt(vector<int> a, Frames& F) {
    /* 先用真 AdaptSort 决策（不计帧），再回放对应策略的动画。
     * 注意 a 会 adapt_sort 原地排好 —— 必须保留一份原始序列给策略动画。 */
    vector<int> orig = a;
    adapt_sort(a.data(), (int)a.size());
    Strat st = last_strategy();
    F.strategy = strat_name(st);
    switch (st) {
        case Strat::AlreadyAscending: F.push(orig, 0, 0, -1, -1); F.push(a, 0, 0, -1, -1); return;  /* 直通 */
        case Strat::ReverseToAscending: {
            long long c = 0, m = 0; int n = (int)orig.size();
            for (int i = 0, j = n - 1; i < j; i++, j--) { swap(orig[i], orig[j]); m += 3; F.push(orig, c, m, i, j); }
            F.push(orig, c, m, -1, -1); return;
        }
        case Strat::CountingSort:      anim_counting(orig, F); return;
        case Strat::RadixLSD:          anim_radix(orig, F); return;
        case Strat::Quick3Way:
        case Strat::IntroSort:         anim_quick(orig, F); return;
        case Strat::MergeSorted:       anim_merge(orig, F); return;
        default:                       anim_insertion(orig, F); return;
    }
}

static AnimFn animOf(const string& algo) {
    if (algo == "insertion") return anim_insertion;
    if (algo == "bubble")    return anim_bubble;
    if (algo == "selection") return anim_selection;
    if (algo == "shell")     return anim_shell;
    if (algo == "quick")     return anim_quick;
    if (algo == "heap")      return anim_heap;
    if (algo == "radix")     return anim_radix;
    if (algo == "merge")     return anim_merge;
    if (algo == "adapt")     return anim_adapt;
    return nullptr;
}

/* ================================================================
 * 二、真实数据排序矩阵（评分点 8 核心证据） → results/results_real.csv
 * ================================================================ */
/* 顺序无关的 multiset 校验（求和 + 异或组合）：
 * 外部排序的输入序与输出序不同，不能用 FNV 之类的顺序敏感哈希验多重度。 */
static void multisetSum(const vector<long long>& a, unsigned long long& s1, unsigned long long& sx) {
    s1 = 0; sx = 0;
    for (long long x : a) { s1 += (unsigned long long)x; sx ^= (unsigned long long)x; }
}

static string adaptStrategyNameLL() {
    string s = strat_name(last_strategy());
    if (last_strategy() == Strat::RadixLSD && last_radix_db() == 8) s += "(8位)";
    return s;
}

static void adaptWrapLL(long long* a, int n, int order, SortStats* st) {
    if (st) { counters().reset(); counters().on = true; mem().reset(); }
    adapt_sort(a, n);
    if (order == -1) reverse_array(a, n);
    if (st) {
        stats_init(st, order, "AdaptSort");
        st->compares = (long long)counters().cmp;
        st->moves = (long long)counters().mv;
        st->aux_peak = (long long)mem().peak;
        counters().on = false;
    }
}

static void stdSortWrapLL(long long* a, int n, int order, SortStats* st) {
    if (st) stats_init(st, order, "std::sort");
    if (order == 1) std::sort(a, a + n);
    else            std::sort(a, a + n, greater<long long>());
    if (st) { st->moves = -1; st->aux_peak = -1; }
}

static vector<long long> loadKeys(const string& path) {
    vector<long long> v;
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return v;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    v.resize(sz / 8);
    fread(v.data(), 8, v.size(), f);
    fclose(f);
    return v;
}

static int runMatrix(const string& tag) {
    struct Src { const char* file; };
    Src srcs[] = { {"postings_1e5.dat"}, {"postings_1e6.dat"}, {"postings.dat"}, {"score.dat"} };
    string outCsv = g_resdir + (tag == "real" ? "/results_real.csv" : "/results_real_" + tag + ".csv");
    FILE* csv = fopen(outCsv.c_str(), "wb");
    if (!csv) { printf("cannot open results_real.csv\n"); return 1; }
    fprintf(csv, "lang,dist,algo,n,reps,batch,tmin_ms,tmed_ms,tstd_ms,tcnt_ms,cmp,mv,aux_peak,ok,strategy\n");

    struct A { const char* id; sg::SortFnK<long long> fn; bool quad; };
    A algos[] = {
        {"insertion", sg::insertionSort<long long>, true},
        {"bubble",    sg::bubbleSort<long long>,    true},
        {"selection", sg::selectSort<long long>,    true},
        {"shell",     sg::shellSort<long long>,     false},
        {"quick",     sg::quickSort<long long>,     false},
        {"heap",      sg::heapSort<long long>,      false},
        {"radix",     sg::radixSort<long long>,     false},
        {"merge",     sg::mergeSort<long long>,     false},
        {"adapt",     adaptWrapLL,                  false},
        {"stdsort",   stdSortWrapLL,                false},
    };
    const int QUAD_CAP = 150000;   /* 1e5 抽样档：ml-25m 约 10.0 万行 / ml-32m 约 12.8 万行，都要实跑 */
    SortStats st; double tcnt;

    for (Src& s : srcs) {
        vector<long long> base = loadKeys(string(g_datadir) + "/" + s.file);
        if ((int)base.size() < 10000) continue;
        int n = (int)base.size();
        printf("\n==== %s : n=%d ====\n", s.file, n);
        printf("%-10s %12s %12s %16s %16s %12s\n", "algo", "t_min/ms", "t_med/ms", "cmp", "mv", "aux_B");
        for (A& a : algos) {
            if (a.quad && n > QUAD_CAP) {
                fprintf(csv, "cpp,real,%s,%d,5,1,-1,-1,-1,-1,-1,-1,-1,1,\n", a.id, n);
                printf("%-10s %12s (n>1e5 不实跑)\n", a.id, "—");
                continue;
            }
            sg::BenchTimeK bt = sg::bench_time_k<long long>(a.fn, base.data(), n, 1);
            int ok = sg::bench_count_k<long long>(a.fn, base.data(), n, 1, a.id, &st, &tcnt);
            const char* strat = "";
            string stratS;
            if (string(a.id) == "adapt") {
                stratS = adaptStrategyNameLL();
                strat = stratS.c_str();
            }
            fprintf(csv, "cpp,real,%s,%d,%d,%d,%.4f,%.4f,%.4f,%.4f,%lld,%lld,%lld,%d,%s\n",
                    a.id, n, bt.reps, bt.batch, bt.min_ms, bt.med_ms, bt.std_ms, tcnt,
                    st.compares, st.moves, st.aux_peak, ok, strat);
            printf("%-10s %12.4f %12.4f %16lld %16lld %12lld %s\n",
                   a.id, bt.min_ms, bt.med_ms, st.compares, st.moves, st.aux_peak, ok ? "" : "[FAIL]");
        }
    }
    fclose(csv);
    printf("\n真实数据矩阵完成 → %s\n", outCsv.c_str());
    return 0;
}

/* ================================================================
 * 三、外部排序 BSBI（评分点 10 扩充一）
 * ================================================================ */
static int runExtSort(const string& tag) {
    string in = g_datadir + "/postings.dat";
    string outp = g_datadir + "/postings_sorted.dat";
    vector<long long> all = loadKeys(in);
    int N = (int)all.size();
    if (N < 1000) { printf("postings.dat 太小，跳过外部排序演示\n"); return 1; }
    unsigned long long sumIn = 0, xorIn = 0;
    multisetSum(all, sumIn, xorIn);
    int mem = N / 10;                    /* 内存限流 = 数据量的 1/10 */
    printf("外部排序: N=%d keys, 内存限流=%d keys (%.1f MB), 共 %d 块\n",
           N, mem, mem * 8.0 / 1048576, (N + mem - 1) / mem);

    long long ioBlocks = 0;
    /* Phase 1: 块内排序 → 生成有序游程 */
    vector<string> runs;
    vector<long long> buf(mem);
    for (int lo = 0; lo < N; lo += mem) {
        int len = min(mem, N - lo);
        memcpy(buf.data(), all.data() + lo, sizeof(long long) * len);
        SortStats st;
        double t0 = now_ms();
        adaptWrapLL(buf.data(), len, 1, &st);          /* 块内用 AdaptSort */
        double t1 = now_ms();
        st.time_ms = t1 - t0;
        char name[64];
        snprintf(name, sizeof(name), "%s/run_%02d.dat", g_datadir.c_str(), (int)runs.size());
        FILE* w = fopen(name, "wb");
        fwrite(buf.data(), 8, len, w);
        fclose(w);
        runs.push_back(name);
        ioBlocks++;
        printf("  run %2d/%d: %d keys (块内 %s, %.2f ms)\n",
               (int)runs.size(), (N + mem - 1) / mem, len, strat_name(last_strategy()), st.time_ms);
    }

    /* Phase 2: k 路堆归并（k = 游程数），每路 64KB 缓冲 */
    int k = (int)runs.size();
    vector<FILE*> rf(k);
    vector<vector<long long>> rb(k);
    vector<int> rp(k, 0), rn(k, 0), eofr(k, 0);
    for (int i = 0; i < k; i++) {
        rf[i] = fopen(runs[i].c_str(), "rb");
        rb[i].resize(8192);
        rn[i] = (int)fread(rb[i].data(), 8, 8192, rf[i]);
        ioBlocks++;                       /* 每次缓冲重填记 1 块读 */
        rp[i] = 0;
    }
    struct Node { long long key; int run; };
    vector<Node> heap;
    auto lessNode = [](const Node& a, const Node& b) {
        return a.key != b.key ? a.key < b.key : a.run < b.run;
    };
    for (int i = 0; i < k; i++)
        if (rn[i] > 0) { heap.push_back({rb[i][rp[i]++], i}); }
    make_heap(heap.begin(), heap.end(), [&](const Node& a, const Node& b) { return lessNode(b, a); });

    FILE* out = fopen(outp.c_str(), "wb");
    long long written = 0;
    while (!heap.empty()) {
        pop_heap(heap.begin(), heap.end(), [&](const Node& a, const Node& b) { return lessNode(b, a); });
        Node top = heap.back(); heap.pop_back();
        fwrite(&top.key, 8, 1, out);
        written++;
        int r = top.run;
        if (rp[r] >= rn[r]) {
            if (!eofr[r]) {
                rn[r] = (int)fread(rb[r].data(), 8, 8192, rf[r]);
                ioBlocks++;
                rp[r] = 0;
                if (rn[r] == 0) { eofr[r] = 1; fclose(rf[r]); }
            }
        }
        if (!eofr[r] && rp[r] < rn[r]) {
            heap.push_back({rb[r][rp[r]++], r});
            push_heap(heap.begin(), heap.end(), [&](const Node& a, const Node& b) { return lessNode(b, a); });
        }
    }
    fclose(out);
    for (int i = 0; i < k; i++) if (rf[i]) fclose(rf[i]);

    /* 校验 */
    vector<long long> res = loadKeys(outp);
    unsigned long long sumOut = 0, xorOut = 0;
    multisetSum(res, sumOut, xorOut);
    int sortedOk = sg::checkSorted<long long>(res.data(), (int)res.size(), 1);
    int multiOk = (sumIn == sumOut && xorIn == xorOut);
    for (auto& p : runs) remove(p.c_str());

    printf("\n结果: 输出 %lld keys, 有序=%s, multiset一致=%s\n",
           written, sortedOk ? "OK" : "FAIL", multiOk ? "OK" : "FAIL");
    printf("I/O 统计: 写块 %lld + 读块 %lld = %lld 块 (每块 %d keys)\n",
           (long long)runs.size(), ioBlocks - (long long)runs.size(), ioBlocks, mem > 8192 ? 8192 : mem);
    printf("对比: 内存排序需 0 次额外 I/O；外部排序以 %.1f 倍块 I/O 换取 1/10 内存占用\n",
           (double)ioBlocks / max(1LL, (long long)(N * 8 / (8192LL * 8))));
    string outRep = g_resdir + (tag == "real" ? "/results_extsort.txt" : "/results_extsort_" + tag + ".txt");
    FILE* rep = fopen(outRep.c_str(), "wb");
    if (rep) {
        fprintf(rep, "外部排序(BSBI) 报告\nN=%d\n内存限流=%d keys (1/10)\n游程数=%d\n"
                     "写块=%lld 读块=%lld\n输出有序=%d multiset一致=%d\n",
                N, mem, k, (long long)runs.size(), ioBlocks - runs.size(), sortedOk, multiOk);
        fclose(rep);
    }
    return (sortedOk && multiOk) ? 0 : 1;
}

/* ================================================================
 * 四、Top-K 选择对比（评分点 10 扩充二）
 * ================================================================ */
static long long g_cmpcnt;
static void heapSelect(vector<long long>& keys, int k) {  /* 小顶堆留 k 个最大 */
    int n = (int)keys.size();
    if (n <= k) return;
    auto cmpv = [](long long a, long long b) { g_cmpcnt++; return a > b; }; /* >0: a更差 */
    auto worse = [&](long long a, long long b) { return cmpv(a, b) > 0; };
    make_heap(keys.begin(), keys.begin() + k, worse);
    for (int i = k; i < n; i++) {
        g_cmpcnt++;                       /* 与堆顶的一次比较 */
        if (keys[i] > keys[0]) {
            pop_heap(keys.begin(), keys.begin() + k, worse);
            keys[k - 1] = keys[i];
            push_heap(keys.begin(), keys.begin() + k, worse);
        }
    }
}
static void quickSelectDesc(vector<long long>& a, int k) {  /* 前 k 大移到末尾侧 */
    int lo = 0, hi = (int)a.size() - 1;
    int target = (int)a.size() - k;   /* 升序第 target 位 = 第 k 大 */
    while (lo < hi) {
        swap(a[(lo + hi) / 2], a[hi]);
        long long pivot = a[hi];
        int i = lo;
        for (int j = lo; j < hi; j++) {
            g_cmpcnt++;
            if (a[j] < pivot) { swap(a[i], a[j]); i++; }
        }
        swap(a[i], a[hi]);
        if (i == target) return;
        if (i < target) lo = i + 1; else hi = i - 1;
    }
}

static int runTopK(const string& tag) {
    /* 场景化路径：数据目录含 meta.txt → 通用实体层的候选得分键
     *（与 ed::rank_topk 同编码），同一套选择算法跑任意场景。 */
    struct stat stt;
    if (stat((g_datadir + "/meta.txt").c_str(), &stt) == 0) {
        ed::EntityStore store;
        if (!store.load(g_datadir)) { printf("数据加载失败\n"); return 1; }
        string outCsv = g_resdir + (tag == "real" ? "/results_topk.csv" : "/results_topk_" + tag + ".csv");
        vector<long long> base;
        store.scoreKeys(base, "pop");
        printf("候选集 n=%d 个%s（得分键 = 关注度主键 + 均分次键）\n\n",
               (int)base.size(), store.entityLabel.c_str());
        FILE* csv = fopen(outCsv.c_str(), "wb");
        if (!csv) { printf("cannot open %s\n", outCsv.c_str()); return 1; }
        fprintf(csv, "method,k,n,time_ms,compares\n");
        double t0, t1;
        for (int k : {10, 100, 1000}) {
            printf("---- k=%d ----\n", k);
            vector<long long> a = base; g_cmpcnt = 0;
            t0 = now_ms(); heapSelect(a, k); t1 = now_ms();
            printf("堆选择     : %8.4f ms, 比较次数 %lld\n", t1 - t0, g_cmpcnt);
            fprintf(csv, "heap,%d,%d,%.4f,%lld\n", k, (int)base.size(), t1 - t0, g_cmpcnt);
            a = base; g_cmpcnt = 0;
            t0 = now_ms(); quickSelectDesc(a, k); t1 = now_ms();
            printf("快速选择   : %8.4f ms, 比较次数 %lld\n", t1 - t0, g_cmpcnt);
            fprintf(csv, "qselect,%d,%d,%.4f,%lld\n", k, (int)base.size(), t1 - t0, g_cmpcnt);
            a = base; g_cmpcnt = 0;
            t0 = now_ms();
            SortStats st;
            sg::quickSort<long long>(a.data(), (int)a.size(), 1, &st);
            t1 = now_ms();
            printf("全排序     : %8.4f ms, 比较次数 %lld\n", t1 - t0, st.compares);
            fprintf(csv, "fullsort,%d,%d,%.4f,%lld\n", k, (int)base.size(), t1 - t0, st.compares);
        }
        fclose(csv);
        printf("\nTop-K 对比完成 → %s\n", outCsv.c_str());
        return 0;
    }
    ml::Store store;
    if (!store.load(g_datadir)) { printf("数据加载失败\n"); return 1; }
    /* 候选集 = 全部电影，得分键 = 关注度<<16 | 好评度（与 rank_topk 同编码） */
    vector<long long> base;
    for (auto& m : store.movies) {
        int avgX10 = (int)(m.mean() * 10.0 + 0.5);
        base.push_back(((long long)m.count << 8) | (long long)(63 - avgX10));
    }
    printf("候选集 n=%d部电影（得分键 = 关注度主键 + 好评度次键）\n\n", (int)base.size());
    FILE* csv = fopen((g_resdir + "/results_topk.csv").c_str(), "wb");
    fprintf(csv, "method,k,n,time_ms,compares\n");

    double t0, t1;
    for (int k : {10, 100, 1000}) {
        printf("---- k=%d ----\n", k);
        /* 堆选择 O(n log k) */
        vector<long long> a = base;
        g_cmpcnt = 0;
        t0 = now_ms(); heapSelect(a, k); t1 = now_ms();
        printf("堆选择     : %8.4f ms, 比较次数 %lld\n", t1 - t0, g_cmpcnt);
        fprintf(csv, "heap,%d,%d,%.4f,%lld\n", k, (int)base.size(), t1 - t0, g_cmpcnt);
        /* 快速选择 O(n) */
        a = base;
        g_cmpcnt = 0;
        t0 = now_ms(); quickSelectDesc(a, k); t1 = now_ms();
        printf("快速选择   : %8.4f ms, 比较次数 %lld\n", t1 - t0, g_cmpcnt);
        fprintf(csv, "qselect,%d,%d,%.4f,%lld\n", k, (int)base.size(), t1 - t0, g_cmpcnt);
        /* 全排序 O(n log n) */
        a = base;
        g_cmpcnt = 0;
        t0 = now_ms();
        SortStats st;
        sg::quickSort<long long>(a.data(), (int)a.size(), 1, &st);
        t1 = now_ms();
        printf("全排序     : %8.4f ms, 比较次数 %lld\n", t1 - t0, st.compares);
        fprintf(csv, "fullsort,%d,%d,%.4f,%lld\n", k, (int)base.size(), t1 - t0, st.compares);
    }
    fclose(csv);
    printf("\nTop-K 对比完成 → results/results_topk.csv\n");
    return 0;
}

/* ================================================================
 * 五、正确性自检
 * ================================================================ */
static int runSelfTest() {
    int fails = 0;
    struct A { const char* id; sg::SortFnK<long long> fn; };
    A algos[] = {
        {"insertion", sg::insertionSort<long long>}, {"bubble", sg::bubbleSort<long long>},
        {"selection", sg::selectSort<long long>},    {"shell", sg::shellSort<long long>},
        {"quick", sg::quickSort<long long>},         {"heap", sg::heapSort<long long>},
        {"radix", sg::radixSort<long long>},         {"merge", sg::mergeSort<long long>},
        {"adapt", adaptWrapLL},                      {"stdsort", stdSortWrapLL},
    };
    printf("int64 合成数据正确性（×5 分布 × 双向 × 6 规模, 参照 std::sort）\n");
    for (A& a : algos) {
        int bad = 0;
        for (unsigned d = 0; d < DIST_COUNT; d++)
            for (int order = 1; order >= -1; order -= 2)
                for (int n : {0, 1, 2, 5, 100, 1000}) {
                    vector<long long> a1(n), ref(n);
                    g_drng = 12345u + (unsigned)n * 31u + (unsigned)order;
                    for (int i = 0; i < n; i++) a1[i] = (long long)(drng01() * 1000000);
                    if (d == DIST_SORTED_ASC) for (int i = 0; i < n; i++) a1[i] = i + 1;
                    if (d == DIST_SORTED_DESC) for (int i = 0; i < n; i++) a1[i] = n - i;
                    if (d == DIST_POISSON) for (int i = 0; i < n; i++) a1[i] = (long long)(drng01() * 39) + 5;
                    ref = a1;
                    std::sort(ref.begin(), ref.end());
                    if (order == -1) std::reverse(ref.begin(), ref.end());
                    a.fn(a1.data(), n, order, nullptr);
                    if (!sg::checkSorted<long long>(a1.data(), n, order) ||
                        (n > 0 && memcmp(a1.data(), ref.data(), sizeof(long long) * n) != 0)) bad++;
                }
        printf("  %-10s : %s\n", a.id, bad ? "FAIL" : "OK (60 组)");
        fails += bad;
    }
    /* 真实数据正确性（抽样 20 万键：正确性验证不需要全量规模，
     * 全量 25M 键喂给 Θ(n²) 算法要跑数小时以上。
     * 数据文件回退：无全量 postings.dat 时依次回退 1e6/1e5 抽样档） */
    const char* keyFiles[] = { "/postings.dat", "/postings_1e6.dat", "/postings_1e5.dat" };
    vector<long long> full;
    for (const char* kf : keyFiles) {
        full = loadKeys(g_datadir + kf);
        if (!full.empty()) break;
    }
    if (!full.empty()) {
        vector<long long> base(full.begin(),
                               full.begin() + min<size_t>(full.size(), 200000));
        full.clear();
        vector<long long> ref = base;
        std::sort(ref.begin(), ref.end());
        printf("真实数据正确性（postings.dat 抽样 n=%d）\n", (int)base.size());
        for (A& a : algos) {
            vector<long long> a1 = base;
            a.fn(a1.data(), (int)a1.size(), 1, nullptr);
            int ok = sg::checkSorted<long long>(a1.data(), (int)a1.size(), 1) &&
                     memcmp(a1.data(), ref.data(), sizeof(long long) * a1.size()) == 0;
            printf("  %-10s : %s\n", a.id, ok ? "OK" : "FAIL");
            fails += !ok;
        }
    }
    /* 计数自检（K=long long, 理论值） */
    printf("计数自检（理论值对照）\n");
    {
        SortStats st;
        vector<long long> a(1000);
        for (int i = 0; i < 1000; i++) a[i] = i + 1;
        sg::insertionSort<long long>(a.data(), 1000, 1, &st);
        printf("  插入/正序: cmp=%lld (期望999) mv=%lld (期望1998) %s\n",
               st.compares, st.moves,
               (st.compares == 999 && st.moves == 1998) ? "OK" : "FAIL");
        fails += !(st.compares == 999 && st.moves == 1998);
        for (int i = 0; i < 1000; i++) a[i] = i + 1;
        sg::radixSort<long long>(a.data(), 1000, 1, &st);
        printf("  基数/正序: cmp=%lld (期望0) aux=%lld (期望8000) %s\n",
               st.compares, st.aux_peak,
               (st.compares == 0 && st.aux_peak == 8000) ? "OK" : "FAIL");
        fails += !(st.compares == 0 && st.aux_peak == 8000);
    }
    printf("自检完成：%s\n", fails ? "存在 FAIL" : "全部通过");
    return fails ? 1 : 0;
}

/* ================================================================
 * 六、HTTP 服务器（评分点 9）
 * ================================================================ */
static string jsonEscape(const string& s) {
    string r;
    for (char ch : s) {
        if (ch == '"' || ch == '\\') { r += '\\'; r += ch; }
        else if (ch == '\n') r += "\\n";
        else if (ch == '\r') r += "\\r";
        else if (ch == '\t') r += "\\t";
        else if ((unsigned char)ch < 0x20) { char b[8]; snprintf(b, 8, "\\u%04x", ch); r += b; }
        else r += ch;
    }
    return r;
}

static string readFileOr(const string& path, const string& fallbackMsg) {
    ifstream f(path, ios::binary);
    if (!f) return fallbackMsg;
    stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

static ml::Store g_store;

/* ---------------- 多场景注册表（多场景应用方案 1.2） ----------------
 * 扫描 g_datadir 的子目录：含 meta.txt 的子目录即一个场景，
 * 由通用实体层 ed::EntityStore 按各场景的键位布局解码加载。 */
static std::map<std::string, ed::EntityStore> g_scn;

static void loadScenarios() {
    g_scn.clear();
    DIR* d = opendir(g_datadir.c_str());
    if (!d) return;
    struct dirent* e;
    while ((e = readdir(d)) != nullptr) {
        string nm = e->d_name;
        if (nm.empty() || nm[0] == '.') continue;
        string sub = g_datadir + "/" + nm;
        struct stat st;
        if (stat(sub.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) continue;
        ed::EntityStore store;
        if (store.load(sub)) {
            printf("[scn] %-8s %-24s %zu 实体 / %lld 键\n",
                   store.scn.c_str(), store.name.c_str(),
                   store.ents.size(), store.nPostings);
            g_scn[store.scn] = std::move(store);
        }
    }
    closedir(d);
    sa::initGeneSearch(g_datadir);
}

static ed::EntityStore* scnStore(const string& scn) {
    auto it = g_scn.find(scn);
    return it == g_scn.end() ? nullptr : &it->second;
}

/* 场景名白名单：仅小写字母/数字/下划线，防止路径穿越 */
static bool scnOk(const string& s) {
    if (s.empty() || s.size() > 24) return false;
    for (char c : s)
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_')) return false;
    return true;
}

static string apiStats() {
    ostringstream o;
    o << "{\"postings\":" << g_store.nPostings
      << ",\"movies\":" << g_store.movies.size()
      << ",\"maxUser\":" << g_store.maxUserId
      << ",\"yearHist\":[";
    for (size_t i = 0; i < g_store.yearHist.size(); i++)
        o << (i ? "," : "") << "[" << g_store.yearHist[i].first << "," << g_store.yearHist[i].second << "]";
    o << "],\"genreHist\":[";
    for (size_t i = 0; i < g_store.genreHist.size(); i++)
        o << (i ? "," : "") << "[\"" << jsonEscape(g_store.genreHist[i].first) << "\","
          << g_store.genreHist[i].second << "]";
    o << "]}";
    return o.str();
}

static string apiSearch(const string& q, int limit) {
    auto hits = g_store.search(q, limit);
    ostringstream o;
    o << "{\"q\":\"" << jsonEscape(q) << "\",\"total\":" << hits.size() << ",\"items\":[";
    for (size_t i = 0; i < hits.size(); i++) {
        const ml::MovieRow* m = hits[i].mv;
        o << (i ? "," : "") << "{\"id\":" << m->id
          << ",\"title\":\"" << jsonEscape(m->title) << "\""
          << ",\"year\":" << m->year
          << ",\"genre\":\"" << jsonEscape(g_store.genreNames[m->genreId]) << "\""
          << ",\"count\":" << m->count
          << ",\"mean\":" << m->mean() << "}";
    }
    o << "]}";
    return o.str();
}

static string apiTopK(int k, const string& genre, long long minVotes, const string& mode) {
    int genreId = -1;
    for (size_t i = 0; i < g_store.genreNames.size(); i++)
        if (g_store.genreNames[i] == genre) genreId = (int)i;
    auto rows = g_store.rank_topk(k, genreId, minVotes, mode.c_str());
    ostringstream o;
    o << "{\"mode\":\"" << mode << "\",\"genre\":\"" << jsonEscape(genre)
      << "\",\"minVotes\":" << minVotes << ",\"items\":[";
    for (size_t i = 0; i < rows.size(); i++) {
        const ml::MovieRow* m = rows[i];
        o << (i ? "," : "") << "{\"rank\":" << i + 1
          << ",\"id\":" << m->id
          << ",\"title\":\"" << jsonEscape(m->title) << "\""
          << ",\"year\":" << m->year
          << ",\"genre\":\"" << jsonEscape(g_store.genreNames[m->genreId]) << "\""
          << ",\"count\":" << m->count
          << ",\"mean\":" << m->mean() << "}";
    }
    o << "]}";
    return o.str();
}

static string apiGenres() {
    ostringstream o;
    o << "{\"genres\":[";
    for (size_t i = 0; i < g_store.genreNames.size(); i++)
        o << (i ? "," : "") << "\"" << jsonEscape(g_store.genreNames[i]) << "\"";
    o << "]}";
    return o.str();
}

/* ---------------- 多场景 API ---------------- */
static string apiScnList() {
    ostringstream o;
    o << "{\"scenarios\":[";
    bool first = true;
    for (auto& kv : g_scn) {
        const ed::EntityStore& st = kv.second;
        if (!first) o << ",";
        first = false;
        o << "{\"scn\":\"" << jsonEscape(kv.first)
          << "\",\"name\":\"" << jsonEscape(st.name)
          << "\",\"domain\":\"" << jsonEscape(st.domain)
          << "\",\"entities\":" << st.ents.size()
          << ",\"postings\":" << st.nPostings
          << ",\"entityLabel\":\"" << jsonEscape(st.entityLabel)
          << "\",\"countLabel\":\"" << jsonEscape(st.countLabel)
          << "\",\"valLabel\":\"" << jsonEscape(st.valLabel) << "\"}";
    }
    o << "]}";
    return o.str();
}

static string apiScnStats(const string& scn) {
    ed::EntityStore* st = scnStore(scn);
    if (!st) return "{\"error\":\"unknown scn\"}";
    ostringstream o;
    o << "{\"scn\":\"" << jsonEscape(scn)
      << "\",\"name\":\"" << jsonEscape(st->name)
      << "\",\"postings\":" << st->nPostings
      << ",\"entities\":" << st->ents.size()
      << ",\"maxSub\":" << st->maxSub
      << ",\"entityLabel\":\"" << jsonEscape(st->entityLabel)
      << "\",\"subLabel\":\"" << jsonEscape(st->subLabel)
      << "\",\"countLabel\":\"" << jsonEscape(st->countLabel)
      << "\",\"valLabel\":\"" << jsonEscape(st->valLabel)
      << "\",\"yearHist\":[";
    for (size_t i = 0; i < st->yearHist.size(); i++)
        o << (i ? "," : "") << "[" << st->yearHist[i].first << "," << st->yearHist[i].second << "]";
    o << "],\"catHist\":[";
    for (size_t i = 0; i < st->catHist.size(); i++)
        o << (i ? "," : "") << "[\"" << jsonEscape(st->catHist[i].first) << "\"," << st->catHist[i].second << "]";
    o << "]}";
    return o.str();
}

static string apiScnCats(const string& scn) {
    ed::EntityStore* st = scnStore(scn);
    if (!st) return "{\"error\":\"unknown scn\"}";
    ostringstream o;
    o << "{\"cats\":[";
    for (size_t i = 0; i < st->catNames.size(); i++)
        o << (i ? "," : "") << "\"" << jsonEscape(st->catNames[i]) << "\"";
    o << "]}";
    return o.str();
}

static string apiScnSearch(const string& scn, const string& q, int limit) {
    ed::EntityStore* st = scnStore(scn);
    if (!st) return "{\"error\":\"unknown scn\"}";
    auto hits = st->search(q, limit);
    ostringstream o;
    o << "{\"scn\":\"" << jsonEscape(scn) << "\",\"q\":\"" << jsonEscape(q)
      << "\",\"total\":" << hits.size() << ",\"items\":[";
    for (size_t i = 0; i < hits.size(); i++) {
        const ed::EntityRow* e = hits[i].ent;
        o << (i ? "," : "") << "{\"id\":" << e->id
          << ",\"name\":\"" << jsonEscape(e->name) << "\""
          << ",\"year\":" << e->year
          << ",\"cat\":\"" << jsonEscape(st->catNames[e->catId]) << "\""
          << ",\"count\":" << e->count
          << ",\"val\":" << e->val
          << ",\"mean\":" << st->meanOf(*e) << "}";
    }
    o << "]}";
    return o.str();
}

static string apiScnTopk(const string& scn, int k, const string& cat,
                         long long minCount, const string& mode) {
    ed::EntityStore* st = scnStore(scn);
    if (!st) return "{\"error\":\"unknown scn\"}";
    int catId = -1;
    for (size_t i = 0; i < st->catNames.size(); i++)
        if (st->catNames[i] == cat) catId = (int)i;
    auto rows = st->rank_topk(k, catId, minCount, mode.c_str());
    ostringstream o;
    o << "{\"scn\":\"" << jsonEscape(scn) << "\",\"mode\":\"" << mode
      << "\",\"cat\":\"" << jsonEscape(cat) << "\",\"minCount\":" << minCount
      << ",\"countLabel\":\"" << jsonEscape(st->countLabel)
      << "\",\"valLabel\":\"" << jsonEscape(st->valLabel) << "\",\"items\":[";
    for (size_t i = 0; i < rows.size(); i++) {
        const ed::EntityRow* e = rows[i];
        o << (i ? "," : "") << "{\"rank\":" << i + 1
          << ",\"id\":" << e->id
          << ",\"name\":\"" << jsonEscape(e->name) << "\""
          << ",\"year\":" << e->year
          << ",\"cat\":\"" << jsonEscape(st->catNames[e->catId]) << "\""
          << ",\"count\":" << e->count
          << ",\"val\":" << e->val
          << ",\"mean\":" << st->meanOf(*e) << "}";
    }
    o << "]}";
    return o.str();
}

static string apiSortSteps(const string& algo, int n, const string& dist, int order) {
    if (n < 2) n = 2;
    if (n > 10000) n = 10000;
    vector<int> a(n);
    unsigned seed = 20260928u;
    if (dist == "near") {          /* 近有序：升序 + 少量相邻交换 */
        for (int i = 0; i < n; i++) a[i] = i + 1;
        g_drng = seed;
        for (int s = 0; s < max(1, n / 20); s++) {
            int i = (int)(drng01() * (n - 1));
            swap(a[i], a[i + 1]);
        }
    } else if (dist == "dup") {    /* 高重复：少量取值 */
        g_drng = seed;
        for (int i = 0; i < n; i++) a[i] = (int)(drng01() * 5) * 10 + 5;
    } else if (dist == "negative") { /* 有符号值：验证基数最高字节的符号位 */
        g_drng = seed;
        for (int i = 0; i < n; i++) a[i] = (int)(drng01() * 20001.0) - 10000;
    } else if (dist == "wide") {   /* 宽值域 0~9999：跨两个字节，基数多趟过程可见 */
        g_drng = seed;
        for (int i = 0; i < n; i++) a[i] = (int)(drng01() * 10000);
    } else if (dist == "asc") {
        for (int i = 0; i < n; i++) a[i] = i + 1;
    } else if (dist == "desc") {
        for (int i = 0; i < n; i++) a[i] = n - i;
    } else {                       /* uniform */
        g_drng = seed;
        for (int i = 0; i < n; i++) a[i] = (int)(drng01() * 100);
    }
    AnimFn fn = animOf(algo);
    Frames F;
    F.budget = max(200, min(720, 1600000 / max(n, 1)));   /* 大 n 抽帧，控制 JSON 体积 */
    if (fn) fn(a, F);
    ostringstream o;
    o << "{\"algo\":\"" << algo << "\",\"strategy\":\"" << jsonEscape(F.strategy)
      << "\",\"n\":" << n << ",\"order\":" << order << ",\"frames\":[";
    for (size_t f = 0; f < F.arrs.size(); f++) {
        o << (f ? "," : "") << "[";
        for (size_t i = 0; i < F.arrs[f].size(); i++) o << (i ? "," : "") << F.arrs[f][i];
        o << "]";
    }
    o << "],\"meta\":[";
    for (size_t f = 0; f < F.meta.size(); f++)
        o << (f ? "," : "") << "[" << F.meta[f][0] << "," << F.meta[f][1] << ","
          << F.meta[f][2] << "," << F.meta[f][3] << "]";
    o << "]}";
    return o.str();
}

/* 现场单格运行（int 合成数据，复用 common 的基准框架） */
static void adaptWrapInt(int* a, int n, int order, SortStats* st) {
    if (st) { counters().reset(); counters().on = true; mem().reset(); }
    adapt_sort(a, n);
    if (order == -1) {
        for (int i = 0, j = n - 1; i < j; i++, j--) { int t = a[i]; a[i] = a[j]; a[j] = t; }
    }
    if (st) {
        stats_init(st, order, "AdaptSort");
        st->compares = (long long)counters().cmp;
        st->moves = (long long)counters().mv;
        st->aux_peak = (long long)mem().peak;
        counters().on = false;
    }
}

static string apiSortRun(const string& algo, int n, const string& dist) {
    struct A { const char* id; SortFunc fn; };
    A algos[] = {
        {"insertion", insertionSort}, {"bubble", bubbleSort}, {"selection", selectSort},
        {"shell", shellSort}, {"quick", quickSort}, {"heap", heapSort},
        {"radix", radixSort}, {"merge", mergeSort}, {"adapt", adaptWrapInt},
    };
    SortFunc fn = nullptr;
    for (A& a : algos) if (algo == a.id) fn = a.fn;
    if (!fn || n < 100) return "{\"error\":\"bad algo or n<100\"}";
    if (n > 1000000) n = 1000000;
    DistType d = DIST_RANDOM_UNIFORM;
    if (dist == "gauss") d = DIST_GAUSSIAN;
    else if (dist == "poisson") d = DIST_POISSON;
    else if (dist == "asc") d = DIST_SORTED_ASC;
    else if (dist == "desc") d = DIST_SORTED_DESC;
    unsigned seed = 1000003u * (unsigned)n + 97u;
    BenchTime bt = bench_time(fn, n, 1, d, seed);
    SortStats st;
    int ok = bench_count(fn, n, 1, d, seed, algo.c_str(), &st, nullptr);
    ostringstream o;
    o << "{\"algo\":\"" << algo << "\",\"n\":" << n << ",\"dist\":\"" << dist
      << "\",\"min_ms\":" << bt.min_ms << ",\"med_ms\":" << bt.med_ms
      << ",\"std_ms\":" << bt.std_ms << ",\"cmp\":" << st.compares
      << ",\"mv\":" << st.moves << ",\"aux\":" << st.aux_peak
      << ",\"ok\":" << ok << "}";
    return o.str();
}

static string apiBenchData(const string& name) {
    if (name == "c") return readFileOr(g_resdir + "/results_c.csv", "");
    if (name == "cpp") return readFileOr(g_resdir + "/results_cpp.csv", "");
    if (name == "real") return readFileOr(g_resdir + "/results_real.csv", "");
    if (name == "topk") return readFileOr(g_resdir + "/results_topk.csv", "");
    if (name == "c_vs_cpp") return readFileOr(g_resdir + "/c_vs_cpp.csv", "");
    /* 多场景：real_<tag> → results_real_<tag>.csv；topk_<tag> → results_topk_<tag>.csv */
    if (name.size() > 5 && name.size() <= 40 && name.compare(0, 5, "real_") == 0) {
        if (scnOk(name.substr(5))) return readFileOr(g_resdir + "/results_" + name + ".csv", "");
        return "";
    }
    if (name.size() > 5 && name.size() <= 40 && name.compare(0, 5, "topk_") == 0) {
        if (scnOk(name.substr(5))) return readFileOr(g_resdir + "/results_" + name + ".csv", "");
        return "";
    }
    return "";
}

#ifdef _WIN32
static void urlDecode(const string& in, string& out) {
    out.clear();
    for (size_t i = 0; i < in.size(); i++) {
        if (in[i] == '%' && i + 2 < in.size()) {
            char b[3] = {in[i + 1], in[i + 2], 0};
            out.push_back((char)strtol(b, nullptr, 16));
            i += 2;
        } else if (in[i] == '+') out.push_back(' ');
        else out.push_back(in[i]);
    }
}

struct Request {
    string path;
    map<string, string> q;
};

static bool handleClient(SOCKET c) {
    string req;
    char buf[4096];
    int r;
    while ((r = recv(c, buf, sizeof(buf) - 1, 0)) > 0) {
        req.append(buf, r);
        if (req.find("\r\n\r\n") != string::npos) break;
        if (req.size() > 65536) break;
    }
    size_t sp1 = req.find(' ');
    size_t sp2 = req.find(' ', sp1 + 1);
    if (sp1 == string::npos || sp2 == string::npos) return false;
    string full = req.substr(sp1 + 1, sp2 - sp1 - 1);
    string path = full, query;
    size_t qm = full.find('?');
    if (qm != string::npos) { path = full.substr(0, qm); query = full.substr(qm + 1); }
    Request R{path};
    {
        size_t pos = 0;
        while (pos < query.size()) {
            size_t amp = query.find('&', pos);
            if (amp == string::npos) amp = query.size();
            string kv = query.substr(pos, amp - pos);
            size_t eq = kv.find('=');
            if (eq != string::npos) {
                string k, v;
                urlDecode(kv.substr(0, eq), k);
                urlDecode(kv.substr(eq + 1), v);
                R.q[k] = v;
            }
            pos = amp + 1;
        }
    }

    string body, ctype = "application/json; charset=utf-8";
    if (R.path == "/" || R.path == "/index.html") {
        body = readFileOr(g_wwwdir + "/index.html", "<h1>index.html missing</h1>");
        ctype = "text/html; charset=utf-8";
    } else if (R.path == "/echarts.min.js") {
        body = readFileOr(g_wwwdir + "/echarts.min.js", "/* missing */");
        ctype = "application/javascript";
    } else if (R.path.compare(0, 8, "/assets/") == 0) {
        string rel = R.path.substr(8);
        bool ok = !rel.empty() && rel.size() <= 96 && rel.find("..") == string::npos;
        for (char c : rel)
            if (ok && !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
                        || c == '_' || c == '-' || c == '.' || c == '/')) ok = false;
        body = ok ? readFileOr(g_wwwdir + "/assets/" + rel, "") : "";
        if (body.empty()) {
            body = "{\"error\":\"not found\"}";
            ctype = "application/json; charset=utf-8";
            string nf = "HTTP/1.1 404 Not Found\r\nContent-Type: " + ctype +
                        "\r\nContent-Length: " + to_string(body.size()) +
                        "\r\nConnection: close\r\n\r\n" + body;
            send(c, nf.c_str(), (int)nf.size(), 0);
            return false;
        }
        if (rel.size() >= 4 && rel.substr(rel.size()-4) == ".jpg") ctype = "image/jpeg";
        else if (rel.size() >= 4 && rel.substr(rel.size()-4) == ".png") ctype = "image/png";
        else if (rel.size() >= 4 && rel.substr(rel.size()-4) == ".svg") ctype = "image/svg+xml";
        else ctype = "application/octet-stream";
    } else if (R.path == "/api/stats") body = apiStats();
    else if (R.path == "/api/genres") body = apiGenres();
    else if (R.path == "/api/movies/search") body = apiSearch(R.q.count("q") ? R.q["q"] : "", R.q.count("limit") ? atoi(R.q["limit"].c_str()) : 10);
    else if (R.path == "/api/movies/topk") body = apiTopK(R.q.count("k") ? atoi(R.q["k"].c_str()) : 20, R.q.count("genre") ? R.q["genre"] : "(all)", R.q.count("minvotes") ? atoll(R.q["minvotes"].c_str()) : 0, R.q.count("mode") ? R.q["mode"] : "pop");
    else if (R.path == "/api/sort/steps") body = apiSortSteps(R.q.count("algo") ? R.q["algo"] : "bubble", R.q.count("n") ? atoi(R.q["n"].c_str()) : 32, R.q.count("dist") ? R.q["dist"] : "uniform", R.q.count("order") ? atoi(R.q["order"].c_str()) : 1);
    else if (R.path == "/api/sort/run") body = apiSortRun(R.q.count("algo") ? R.q["algo"] : "quick", R.q.count("n") ? atoi(R.q["n"].c_str()) : 100000, R.q.count("dist") ? R.q["dist"] : "uniform");
    else if (R.path == "/api/bench/data") { body = apiBenchData(R.q.count("name") ? R.q["name"] : ""); ctype = "text/csv; charset=utf-8"; }
    else if (R.path == "/api/scn/list") body = apiScnList();
    else if (R.path == "/api/scn/meta") {
        string s = R.q.count("scn") ? R.q["scn"] : "";
        body = scnOk(s) ? readFileOr(g_datadir + "/" + s + "/meta.json", "{\"error\":\"no meta\"}")
                        : string("{\"error\":\"bad scn\"}");
    }
    else if (R.path == "/api/scn/stats") body = apiScnStats(R.q.count("scn") ? R.q["scn"] : "");
    else if (R.path == "/api/scn/cats") body = apiScnCats(R.q.count("scn") ? R.q["scn"] : "");
    else if (R.path == "/api/scn/search")
        body = apiScnSearch(R.q.count("scn") ? R.q["scn"] : "",
                            R.q.count("q") ? R.q["q"] : "",
                            R.q.count("limit") ? atoi(R.q["limit"].c_str()) : 10);
    else if (R.path == "/api/scn/topk")
        body = apiScnTopk(R.q.count("scn") ? R.q["scn"] : "",
                          R.q.count("k") ? atoi(R.q["k"].c_str()) : 20,
                          R.q.count("cat") ? R.q["cat"] : "(all)",
                          R.q.count("mincount") ? atoll(R.q["mincount"].c_str()) : 0,
                          R.q.count("mode") ? R.q["mode"] : "pop");
    else if (R.path == "/api/scn/extra") {
        string s = R.q.count("scn") ? R.q["scn"] : "";
        string fl = R.q.count("file") ? R.q["file"] : "";
        bool fok = !fl.empty() && fl.size() <= 48 && fl.find("..") == string::npos;
        for (char c : fl) if (fok && !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '.')) fok = false;
        body = (scnOk(s) && fok) ? readFileOr(g_datadir + "/" + s + "/" + fl, "{}")
                                 : string("{\"error\":\"bad params\"}");
    }
    else if (R.path == "/api/gene/info") body = readFileOr(g_datadir + "/gene/gene_stats.json", "{}");
    else if (R.path == "/api/gene/search") body = sa::apiSearch(R.q.count("pat") ? R.q["pat"] : "");
    else { body = "{\"error\":\"not found\"}"; ctype = "application/json; charset=utf-8";
        string notfound = "HTTP/1.1 404 Not Found\r\nContent-Type: " + ctype + "\r\nContent-Length: " + to_string(body.size()) + "\r\nConnection: close\r\n\r\n" + body;
        send(c, notfound.c_str(), (int)notfound.size(), 0);
        return false;
    }
    string resp = "HTTP/1.1 200 OK\r\nContent-Type: " + ctype +
                  "\r\nContent-Length: " + to_string(body.size()) +
                  "\r\nConnection: close\r\n\r\n" + body;
    size_t off = 0;
    while (off < resp.size()) {
        int s = send(c, resp.data() + off, (int)min<size_t>(65536, resp.size() - off), 0);
        if (s <= 0) break;
        off += s;
    }
    return true;
}

static int runServer() {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) { printf("WSAStartup failed\n"); return 1; }
    printf("[CineRank] 加载数据 (%s) ...\n", g_datadir.c_str());
    if (!g_store.load(g_datadir)) {
        printf("数据加载失败：请先运行 python app/preprocess.py <ml目录> %s\n", g_datadir.c_str());
        return 1;
    }
    printf("[CineRank] movies=%zu postings=%lld genres=%zu\n",
           g_store.movies.size(), g_store.nPostings, g_store.genreNames.size());
    printf("[CineRank] 扫描多场景数据 (%s/*) ...\n", g_datadir.c_str());
    loadScenarios();
    printf("[CineRank] 场景数=%zu\n", g_scn.size());

    SOCKET srv = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(8080);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    int yes = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, (const char*)&yes, sizeof(yes));
    if (bind(srv, (sockaddr*)&addr, sizeof(addr)) < 0) { printf("bind 8080 failed\n"); return 1; }
    listen(srv, 16);
    printf("[CineRank] UI 已就绪:  http://127.0.0.1:8080   (Ctrl+C 退出)\n");
    for (;;) {
        SOCKET c = accept(srv, nullptr, nullptr);
        if (c == INVALID_SOCKET) continue;
        handleClient(c);
        closesocket(c);
    }
    return 0;
}
#endif

/* ================================================================ */
int main(int argc, char** argv) {
#ifdef _WIN32
    SetConsoleOutputCP(65001);
#endif
    string mode = argc > 1 ? argv[1] : "server";
    if (argc > 2) g_datadir = argv[2];
    /* 场景标签：默认 movie 数据目录 → "real"；其他目录 → 目录名（用于结果文件命名） */
    string tag = "real";
    if (g_datadir != "app/data") {
        size_t p = g_datadir.find_last_of("/\\");
        tag = (p == string::npos) ? g_datadir : g_datadir.substr(p + 1);
    }
    if (mode == "selftest") return runSelfTest();
    if (mode == "matrix")   return runMatrix(tag);
    if (mode == "extsort")  return runExtSort(tag);
    if (mode == "topk")     return runTopK(tag);
    if (mode == "pagerank") return wg::runPageRank(g_datadir, g_resdir);
    if (mode == "sufarr")   return sa::runSufArr(g_datadir, g_resdir);
#ifdef _WIN32
    if (mode == "server")   return runServer();
#endif
    printf("用法: sort_demo_app <selftest|matrix|extsort|topk|pagerank|sufarr|server> [data目录]\n");
    return 0;
}
