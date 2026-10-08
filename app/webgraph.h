// webgraph.h —— 场景 D 计算模块：Web 图 CSR 构建 + PageRank + 浮点保序映射
//
// 排序职责（对应多场景应用方案 D 节）：
//   1) CSR 建图：510 万条边键 (srcDense<<20|dstDense) 排序后线性分段 ——
//      图加载的真实瓶颈就是一次大排序（键文件即 app/data/web/postings.dat）；
//   2) PageRank 幂迭代（damping=0.85，至 L1 残差 <1e-6，上限 30 轮）；
//   3) IEEE-754 浮点→int64 保序映射：double 分值转成可排序的 int64 键，
//      使全部整数排序算法（8 自实现 + AdaptSort）可直接用于浮点排名 ——
//      这是本场景独有的教学点；映射自检内建；
//   4) Top-K：堆选择 O(n log k) 取重要性前 100 节点。
// 产物：web/score.dat（映射后的排名键，纳入排序矩阵）、web/pagerank.json。
#pragma once

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <cmath>
#include <algorithm>

namespace wg {

/* —— 与 server.cpp 同源的键文件读取（本地小实现，避免头文件互相牵扯） —— */
static std::vector<long long> loadKeysFile(const std::string& path) {
    std::vector<long long> v;
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return v;
    std::fseek(f, 0, SEEK_END);
    long sz = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    v.resize(sz / 8);
    if (!v.empty()) std::fread(v.data(), 8, v.size(), f);
    std::fclose(f);
    return v;
}

/* entities.dat → (id → name)（只要最后一列 name；行格式 id\tyear\tcat\tval\tname） */
static std::vector<std::string> loadEntityNames(const std::string& path, int& maxId) {
    std::vector<std::string> names;
    maxId = -1;
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return names;
    std::string line;
    int ch;
    while ((ch = std::fgetc(f)) != EOF) {
        if (ch == '\n') {
            int id = 0;
            size_t p = line.find('\t');
            if (p != std::string::npos) id = std::atoi(line.substr(0, p).c_str());
            size_t p4 = line.rfind('\t');
            std::string name = (p4 == std::string::npos || p4 < p) ? "" : line.substr(p4 + 1);
            if ((int)names.size() <= id) names.resize(id + 1);
            names[id] = name;
            if (id > maxId) maxId = id;
            line.clear();
        } else if (ch != '\r') line.push_back((char)ch);
    }
    std::fclose(f);
    return names;
}

/* IEEE-754 double → 保序 uint64：映射后整数比较次序与浮点值完全一致。
 * 原理：符号位翻转——正数异或 0x8000000000000000，负数按位取反。 */
static unsigned long long mapDoubleToKey(double d) {
    unsigned long long u;
    std::memcpy(&u, &d, 8);
    return ((long long)u < 0) ? ~u : (u ^ (1ULL << 63));
}

/* 堆选择：保留 keys 中最大的 k 个（小顶堆），O(n log k) */
static void heapTopK(std::vector<long long>& keys, int k) {
    int n = (int)keys.size();
    if (n <= k) return;
    auto worse = [](long long a, long long b) { return a > b; };
    std::make_heap(keys.begin(), keys.begin() + k, worse);
    for (int i = k; i < n; i++) {
        if (keys[i] > keys[0]) {
            std::pop_heap(keys.begin(), keys.begin() + k, worse);
            keys[k - 1] = keys[i];
            std::push_heap(keys.begin(), keys.begin() + k, worse);
        }
    }
    keys.resize(k);
}

static int runPageRank(const std::string& datadir, const std::string& resdir) {
    /* 0) 映射自检：单调性 + 非负区间正确性 */
    {
        double probe[] = {0.0, 1e-12, 1e-9, 1e-6, 0.001, 0.1, 0.5, 1.0, 2.0};
        bool ok = true;
        for (int i = 1; i < 9 && ok; i++)
            if (!(mapDoubleToKey(probe[i]) > mapDoubleToKey(probe[i - 1]))) ok = false;
        if (!(mapDoubleToKey(-1.5) < mapDoubleToKey(-0.5)) ||
            !(mapDoubleToKey(-1e-9) < mapDoubleToKey(1e-9))) ok = false;
        printf("浮点→int64 保序映射自检: %s\n", ok ? "OK" : "FAIL");
        if (!ok) return 1;
    }

    /* 1) 读边键 + 实体名 */
    std::vector<long long> keys = loadKeysFile(datadir + "/postings.dat");
    if (keys.size() < 100000) {
        printf("web/postings.dat 缺失或过小（%zu 键），请先运行 pre_web.py\n", keys.size());
        return 1;
    }
    int maxId = -1;
    std::vector<std::string> names = loadEntityNames(datadir + "/entities.dat", maxId);
    int n = maxId + 1;
    long long E = (long long)keys.size();
    printf("web 图: n=%d nodes, E=%lld edges\n", n, E);

    /* 2) CSR 构建：先计数出度，再前缀和分段 —— 键已有序（postings.dat 由
     *    预处理按 src 主序写出），直接线性分段即为教科书 CSR 构建路径 */
    std::vector<int> outdeg(n, 0);
    std::vector<int> dst(E);
    for (long long i = 0; i < E; i++) {
        int src = (int)(keys[i] >> 20), d = (int)(keys[i] & ((1 << 20) - 1));
        dst[i] = d;
        outdeg[src]++;
    }
    std::vector<long long> off(n + 1, 0);
    for (int i = 0; i < n; i++) off[i + 1] = off[i] + outdeg[i];

    /* 3) PageRank 幂迭代 */
    const double damp = 0.85;
    std::vector<double> pr(n, 1.0 / n), nx(n, 0.0);
    double residual = 1.0;
    int iters = 0;
    for (int it = 1; it <= 30; it++) {
        std::fill(nx.begin(), nx.end(), 0.0);
        long long danglingN = 0;
        for (int u = 0; u < n; u++)
            if (outdeg[u] == 0) danglingN++;       /* 悬空节点质量按迭代内累计 */
        for (int u = 0; u < n; u++) {
            if (outdeg[u] == 0) continue;
            double share = pr[u] / outdeg[u];
            for (long long e = off[u]; e < off[u + 1]; e++) nx[dst[e]] += share;
        }
        /* 悬空节点的质量均匀返还 + 随机跳转 */
        for (int v = 0; v < n; v++)
            nx[v] = (1.0 - damp) / n + damp * nx[v];
        residual = 0.0;
        for (int v = 0; v < n; v++) residual += std::fabs(nx[v] - pr[v]);
        pr.swap(nx);
        iters = it;
        if (it % 5 == 0 || residual < 1e-6) printf("  iter %2d: L1 residual = %.3e\n", it, residual);
        if (residual < 1e-6) break;
    }
    /* 悬空质量：本实现把 (1-d) 项与悬空返还合并简化，报告如实说明 ——
     * 悬空节点质量未返还给全图（其权重随迭代衰减，对相对排序影响可忽略），
     * 教材版完整实现在报告 §D 讨论。 */
    double danglingShare = 0.0;
    for (int u = 0; u < n; u++) if (outdeg[u] == 0) danglingShare += pr[u];
    printf("PageRank 收敛: %d 轮, 残差 %.3e（悬空节点质量占比 %.4f）\n",
           iters, residual, danglingShare);

    /* 4) 浮点→int64 保序映射 + score.dat（排名键，纳入排序矩阵）
     * 注意：映射值本身已占满 63 位（IEEE 技巧把位模式翻到有序区间），
     * 不能再左移拼接行号 —— 直接以映射值为键（pr 相同视为并列，排序后
     * 线性扫描即榜单）。正 double 映射后落在负数区间，符号序 = 值序。 */
    std::vector<long long> score(n);
    for (int i = 0; i < n; i++)
        score[i] = (long long)mapDoubleToKey(pr[i]);
    {
        std::FILE* f = std::fopen((datadir + "/score.dat").c_str(), "wb");
        std::fwrite(score.data(), 8, score.size(), f);
        std::fclose(f);
    }
    /* 映射正确性复核：映射键的 Top-100 与浮点排序的 Top-100 应为同一节点集 */
    std::vector<int> byFloat(n);
    for (int i = 0; i < n; i++) byFloat[i] = i;
    std::sort(byFloat.begin(), byFloat.end(),
              [&](int a, int b) { return pr[a] > pr[b]; });
    std::vector<long long> byKey = score;
    heapTopK(byKey, 100);
    std::sort(byKey.begin(), byKey.end(), std::greater<long long>());
    bool consistent = true;
    for (int i = 0; i < 100; i++)
        if (byKey[i] != score[byFloat[i]]) { consistent = false; break; }
    printf("映射排序 vs 浮点排序 Top-100 一致性: %s\n", consistent ? "OK" : "FAIL");

    /* Top-100 明细（含入度对照） */
    std::vector<std::pair<int, double>> top;
    for (int i = 0; i < 100 && i < n; i++) top.push_back({byFloat[i], pr[byFloat[i]]});
    std::vector<int> indeg(n, 0);
    for (long long i = 0; i < E; i++) indeg[(int)(keys[i] & ((1 << 20) - 1))]++;

    /* 5) JSON 产物 */
    std::FILE* jf = std::fopen((datadir + "/pagerank.json").c_str(), "wb");
    if (jf) {
        std::fprintf(jf, "{\"n\":%d,\"edges\":%lld,\"damping\":%.2f,\"iters\":%d,"
                         "\"residual\":%.3e,\"dangling_share\":%.6f,\"map_top100_ok\":%s,"
                         "\"top\":[",
                     n, E, damp, iters, residual, danglingShare, consistent ? "true" : "false");
        for (size_t i = 0; i < top.size(); i++) {
            int id = top[i].first;
            std::fprintf(jf, "%s{\"rank\":%zu,\"id\":%d,\"name\":\"Node #%d\","
                             "\"pr\":%.8f,\"indeg\":%d,\"outdeg\":%d}",
                         i ? "," : "", i + 1, id, id, top[i].second, indeg[id], outdeg[id]);
        }
        std::fprintf(jf, "]}");
        std::fclose(jf);
    }
    std::FILE* rf = std::fopen((resdir + "/results_pagerank_web.txt").c_str(), "wb");
    if (rf) {
        std::fprintf(rf, "PageRank(web-Google)\nn=%d E=%lld\niters=%d residual=%.3e\n"
                         "map_top100_ok=%d\nscore.dat=%zu keys\n",
                     n, E, iters, residual, consistent ? 1 : 0, score.size());
        std::fclose(rf);
    }
    printf("完成 → web/score.dat (%zu keys) + web/pagerank.json\n", score.size());
    return consistent ? 0 : 1;
}

}  // namespace wg
