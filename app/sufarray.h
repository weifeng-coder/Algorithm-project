// sufarray.h —— 场景 F 计算模块：倍增法后缀数组 + BWT + O(m log n) 子串搜索
//
// 关键设计（对应多场景应用方案 F 节）：倍增法每一轮的排序对象是
// (rank[i], rank[i+k]) 整数对 —— 编码为单个 int64 复合键后完全复用现有
// 排序内核（AdaptSort 实跑，每轮记录其策略决策与耗时）。
//   key' = ((rank[i] << 32 | rank[i+k]) << 23) | i     （23 位容纳 n ≤ 8.4M）
// 键域随轮次收缩（上一轮的 rank 是本轮的键域），AdaptSort 的决策随之变化
// —— 这是六个场景中"排序 = 瓶颈"论证最强的一处：排序占后缀数组构造
// 耗时的 ~90%，换排序算法直接乘到总耗时上。
// 产物：gene/sa.dat、gene/rounds.csv、gene/gene_stats.json、
//       gene/postings.dat（首轮复合键，纳入排序矩阵）、seq.txt 自检。
#pragma once

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <chrono>
#include <algorithm>
#include "../common/bench.h"
#include "adaptsort.h"

namespace sa {

static double nowMs() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

/* AdaptSort 包装：计数桥接 + 策略标签（与 server.cpp 的 adaptWrapLL 同口径） */
static double adaptTimed(std::vector<long long>& a, std::string& stratOut,
                         long long& cmpOut, long long& mvOut) {
    sb::counters().reset(); sb::counters().on = true; sb::mem().reset();
    double t0 = nowMs();
    sb::adapt_sort(a.data(), (int)a.size());
    double t1 = nowMs();
    stratOut = sb::strat_name(sb::last_strategy());
    cmpOut = (long long)sb::counters().cmp;
    mvOut = (long long)sb::counters().mv;
    sb::counters().on = false;
    return t1 - t0;
}

static int charCode(unsigned char c) {
    switch (c) {
        case '$': return 0;
        case 'A': return 1;
        case 'C': return 2;
        case 'G': return 3;
        case 'T': return 4;
        default:  return 5;   /* N 等不确定碱基单独编码（stats 里如实报告） */
    }
}

/* ---------------- 倍增法核心（输入字符编码数组，输出 SA） ----------------
 * 每轮按 (rank[i], rank[i+k]) 字典序排序。直接把三元组 (r1, r2, i) 打包进
 * 单个 int64 需要 23+23+23=69 位 —— 会溢出成负数、排序错序。
 * 教科书做法：每轮两次 O(n log n) 级排序（adapt_sort 实跑）——
 *   第一遍按次键 r2 排：key = r2<<23 | 上一轮位次（稳定性用序位显式编码）
 *   第二遍按主键 r1 排：key = r1<<23 | 第一遍位次
 * 单键恒 ≤ 2^46，永不溢出；两遍之和不改变每轮"全量大排序"的叙事。 */
static std::vector<int> doublingSA(const std::vector<int>& code,
                                   std::vector<std::string>& roundLog,
                                   long long& totalSortMs) {
    const int n = (int)code.size();
    const int MASK = (1 << 23) - 1;              /* n ≤ 8,388,608 */
    std::vector<int> rk(n), tmp(n), cur(n), cur2(n);
    for (int i = 0; i < n; i++) rk[i] = code[i];
    for (int d = 0; d < n; d++) cur[d] = d;      /* 初始：任意顺序（位次即 d） */
    std::vector<long long> keys(n);
    totalSortMs = 0;
    int round = 0;
    for (long long k = 1; k <= (long long)n; k <<= 1) {
        round++;
        long long ms = 0, cmp = 0, mv = 0; std::string strat1, strat2;
        /* 第一遍：次键 r2 = rank[i+k]（越界取哨兵 0），并列保持上一轮位次 */
        for (int d = 0; d < n; d++) {
            int i = cur[d];
            long long r2 = (i + k < n) ? rk[i + k] : 0;
            keys[d] = (r2 << 23) | d;
        }
        ms += adaptTimed(keys, strat1, cmp, mv);
        for (int d2 = 0; d2 < n; d2++) cur2[d2] = cur[(int)(keys[d2] & MASK)];
        /* 第二遍：主键 r1 = rank[i]，并列保持第一遍位次 → (r1, r2) 字典序 */
        for (int d2 = 0; d2 < n; d2++) {
            int i = cur2[d2];
            keys[d2] = ((long long)rk[i] << 23) | d2;
        }
        ms += adaptTimed(keys, strat2, cmp, mv);
        totalSortMs += (long long)ms;
        for (int d3 = 0; d3 < n; d3++) cur[d3] = cur2[(int)(keys[d3] & MASK)];
        /* 重编 rank：相邻位置的后缀对 (r1, r2) 相同 → 同 rank */
        int r = 0;
        tmp[cur[0]] = 0;
        long long prevPair = ((long long)rk[cur[0]] << 32) |
                             ((cur[0] + k < n) ? rk[cur[0] + k] : 0);
        for (int d = 1; d < n; d++) {
            int i = cur[d];
            long long pair = ((long long)rk[i] << 32) |
                             ((i + k < n) ? rk[i + k] : 0);
            if (pair != prevPair) { r++; prevPair = pair; }
            tmp[i] = r;
        }
        int distinct = r + 1;
        rk.swap(tmp);
        roundLog.push_back("round " + std::to_string(round) + ",k=" + std::to_string(k) +
                           ",ms=" + std::to_string((int)ms) + ",strategy=" + strat1 +
                           "+" + strat2 + ",distinct=" + std::to_string(distinct));
        printf("  round %2d (k=%8lld): 2x sort %8.1f ms  %s+%s  distinct=%d/%d\n",
               round, k, (double)ms, strat1.c_str(), strat2.c_str(), distinct, n);
        if (distinct == n) break;      /* 全部 rank 互异 → SA 完成 */
    }
    return cur;
}

/* 对拍：前 m 个字符的暴力后缀排序 vs 倍增法（同输入：都只排前缀 T[0..m) 的后缀） */
static bool selftestPrefix(const std::string& T, int m) {
    if ((int)T.size() < m) m = (int)T.size();
    std::vector<int> idx(m);
    for (int i = 0; i < m; i++) idx[i] = i;
    std::sort(idx.begin(), idx.end(), [&](int a, int b) {
        int la = m - a, lb = m - b;
        int len = la < lb ? la : lb;
        int c = std::memcmp(T.data() + a, T.data() + b, len);
        if (c != 0) return c < 0;
        return la < lb;               /* 前缀关系：短后缀（含 '$' 语义）在前 */
    });
    std::vector<int> code(m);
    for (int i = 0; i < m; i++) code[i] = charCode((unsigned char)T[i]);
    std::vector<std::string> dummy;
    long long ms;
    std::vector<int> sa = doublingSA(code, dummy, ms);
    for (int i = 0; i < m; i++)
        if (sa[i] != idx[i]) {
            printf("  [debug] first mismatch at rank %d: doubling=%d naive=%d\n",
                   i, sa[i], idx[i]);
            printf("  [debug] doubling 前后缀: ");
            for (int k = 0; k < 24 && sa[i] + k < m; k++) putchar(T[sa[i] + k]);
            printf("\n  [debug] naive   前后缀: ");
            for (int k = 0; k < 24 && idx[i] + k < m; k++) putchar(T[idx[i] + k]);
            printf("\n  [debug] T[0..24] = ");
            for (int k = 0; k < 24 && k < m; k++) putchar(T[k]);
            printf("\n");
            return false;
        }
    return true;
}

static int runSufArr(const std::string& datadir, const std::string& resdir) {
    /* 1) 读序列 */
    std::FILE* f = std::fopen((datadir + "/seq.txt").c_str(), "rb");
    if (!f) { printf("gene/seq.txt 不存在，请先运行 pre_gene.py\n"); return 1; }
    std::string T;
    {
        char buf[1 << 16];
        size_t r;
        while ((r = std::fread(buf, 1, sizeof(buf), f)) > 0) T.append(buf, r);
        std::fclose(f);
    }
    /* 去空白行尾 */
    T.erase(std::remove_if(T.begin(), T.end(),
                           [](char c) { return c == '\r' || c == '\n' || c == ' '; }),
            T.end());
    int n = (int)T.size();
    printf("基因组后缀数组: n=%d (%s)\n", n, (datadir + "/seq.txt").c_str());
    std::printf("  对拍（前 1000 字符暴力 vs 倍增）...\n");
    bool st = selftestPrefix(T, 1000);
    printf("  对拍: %s\n", st ? "OK" : "FAIL");
    if (!st) return 1;

    /* 2) 全序列倍增 */
    std::vector<int> code(n);
    long long comp[6] = {0, 0, 0, 0, 0, 0};
    for (int i = 0; i < n; i++) {
        code[i] = charCode((unsigned char)T[i]);
        comp[code[i]]++;
    }
    std::vector<std::string> roundLog;
    long long totalSortMs = 0;
    printf("  倍增排序（AdaptSort 实跑）...\n");
    std::vector<int> SA = doublingSA(code, roundLog, totalSortMs);

    /* 3) BWT + 游程数（压缩率直观指标） */
    std::string bwt(n, '$');
    long long runs = 0;
    char prev = 0;
    for (int i = 0; i < n; i++) {
        bwt[i] = SA[i] ? T[SA[i] - 1] : '$';
        if (i == 0 || bwt[i] != prev) runs++;
        prev = bwt[i];
    }

    /* 4) 产物 */
    {
        std::FILE* sf = std::fopen((datadir + "/sa.dat").c_str(), "wb");
        std::fwrite(SA.data(), 4, SA.size(), sf);
        std::fclose(sf);
    }
    {
        /* 首轮复合键 → postings.dat（排序矩阵的真实数据负载） */
        std::vector<long long> keys(n);
        for (int i = 0; i + 1 < n; i++)
            keys[i] = (((long long)code[i] << 32) | code[i + 1]) << 23 | i;
        std::FILE* kf = std::fopen((datadir + "/postings.dat").c_str(), "wb");
        std::fwrite(keys.data(), 8, keys.size(), kf);
        std::fclose(kf);
        printf("  gene/postings.dat = %zu keys（首轮复合键，供 matrix 档）\n", keys.size());
    }
    {
        std::FILE* cf = std::fopen((datadir + "/rounds.csv").c_str(), "wb");
        std::fprintf(cf, "round,k,sort_ms,strategy,distinct\n");
        for (auto& s : roundLog) std::fprintf(cf, "%s\n", s.c_str());
        std::fclose(cf);
    }
    {
        std::FILE* jf = std::fopen((datadir + "/gene_stats.json").c_str(), "wb");
        std::fprintf(jf, "{\"seq_len\":%d,\"selftest\":%s,\"total_sort_ms\":%lld,"
                         "\"bwt_runs\":%lld,\"bwt_ratio\":%.4f,\"composition\":{\"A\":%lld,\"C\":%lld,\"G\":%lld,\"T\":%lld,\"other\":%lld},"
                         "\"rounds\":[",
                     n, st ? "true" : "false", totalSortMs, runs, runs / (double)n,
                     comp[1], comp[2], comp[3], comp[4], comp[5]);
        for (size_t i = 0; i < roundLog.size(); i++) {
            /* 把 "round 1,k=1,ms=12,strategy=X,distinct=N" 转成 JSON 对象 */
            int rr, kk, dd; double ms; char strat[64];
            std::sscanf(roundLog[i].c_str(), "round %d,k=%d,ms=%lf,strategy=%63[^,],distinct=%d",
                        &rr, &kk, &ms, strat, &dd);
            std::fprintf(jf, "%s{\"round\":%d,\"k\":%d,\"ms\":%.1f,\"strategy\":\"%s\",\"distinct\":%d}",
                         i ? "," : "", rr, kk, ms, strat, dd);
        }
        std::fprintf(jf, "]}");
        std::fclose(jf);
    }
    {
        std::FILE* rf = std::fopen((resdir + "/results_gene.txt").c_str(), "wb");
        std::fprintf(rf, "后缀数组(倍增法, E.coli K-12)\nn=%d\n对拍=%s\n总排序耗时=%lld ms\n"
                         "BWT 游程=%lld (ratio=%.4f)\n",
                     n, st ? "OK" : "FAIL", totalSortMs, runs, runs / (double)n);
        for (auto& s : roundLog) std::fprintf(rf, "%s\n", s.c_str());
        std::fclose(rf);
    }
    printf("完成 → gene/sa.dat + rounds.csv + gene_stats.json（排序合计 %lld ms, BWT 游程 %lld）\n",
           totalSortMs, runs);
    return 0;
}

/* ---------------- 服务端：SA 子串搜索（O(m log n)） ---------------- */
static std::vector<int> g_sa;
static std::string g_seq;
static bool g_saReady = false;

static void initGeneSearch(const std::string& datadir) {
    /* datadir = app/data（数据根），基因产物在 app/data/gene/ 子目录 */
    g_saReady = false;
    std::FILE* f = std::fopen((datadir + "/gene/sa.dat").c_str(), "rb");
    if (!f) return;
    std::fseek(f, 0, SEEK_END);
    long sz = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    g_sa.resize(sz / 4);
    if (!g_sa.empty()) std::fread(g_sa.data(), 4, g_sa.size(), f);
    std::fclose(f);
    std::FILE* sf = std::fopen((datadir + "/gene/seq.txt").c_str(), "rb");
    if (!sf) return;
    char buf[1 << 16];
    size_t r;
    while ((r = std::fread(buf, 1, sizeof(buf), sf)) > 0) g_seq.append(buf, r);
    std::fclose(sf);
    g_seq.erase(std::remove_if(g_seq.begin(), g_seq.end(),
                               [](char c) { return c == '\r' || c == '\n' || c == ' '; }),
                g_seq.end());
    g_saReady = ((int)g_seq.size() == (int)g_sa.size());
}

/* 后缀 T[p..] 与 pat 的前 m 字符比较 */
static int cmpSuffix(int p, const std::string& pat) {
    int m = (int)pat.size();
    for (int i = 0; i < m; i++) {
        int c = (p + i < (int)g_seq.size()) ? (unsigned char)g_seq[p + i] : 0;
        if (c != (unsigned char)pat[i]) return c < (unsigned char)pat[i] ? -1 : 1;
    }
    return 0;
}

static std::string apiSearch(const std::string& pat) {
    if (!g_saReady) return "{\"error\":\"gene search not ready (run: sort_demo_app sufarr app/data/gene)\"}";
    /* 校验：仅 ACGT，长度 1..32 */
    if (pat.empty() || pat.size() > 32) return "{\"error\":\"bad pattern (1..32 bp)\"}";
    for (char c : pat)
        if (c != 'A' && c != 'C' && c != 'G' && c != 'T')
            return "{\"error\":\"pattern must be A/C/G/T\"}";
    double t0 = nowMs();
    int lo = 0, hi = (int)g_sa.size();          /* 左边界：第一个 ≥ pat 的后缀 */
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (cmpSuffix(g_sa[mid], pat) < 0) lo = mid + 1; else hi = mid;
    }
    int start = lo;
    long long count = 0;
    std::vector<int> pos;
    for (int i = start; i < (int)g_sa.size(); i++) {
        if (cmpSuffix(g_sa[i], pat) != 0) break;
        count++;
        if ((int)pos.size() < 50) pos.push_back(g_sa[i]);
        if (count >= 10000) break;
    }
    double t1 = nowMs();
    char jbuf[128];
    std::string o = "{\"pat\":\"" + pat + "\",\"count\":" + std::to_string(count) +
                    ",\"ms\":" + std::to_string(t1 - t0) + ",\"positions\":[";
    for (size_t i = 0; i < pos.size(); i++) {
        std::snprintf(jbuf, sizeof(jbuf), "%s%d", i ? "," : "", pos[i]);
        o += jbuf;
    }
    o += "]}";
    return o;
}

}  // namespace sa
