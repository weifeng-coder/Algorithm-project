// ml_index.h —— CineRank 数据层与应用逻辑（对应评分点 8）
//
// 应用：基于 MovieLens 25M 真实评分数据的电影检索与排序系统。
// 排序是系统的关键子问题，体现在四处：
//   1) 倒排索引构建：25M 条 posting 键排序后按 movieId 分段聚合（index_build）
//   2) 电影榜单：多级键（关注度↓→好评度↓）编码成 int64 单键排序（rank_topk）
//   3) 影片检索 Top-N：候选打分后堆选择（topn_by_score）
//   4) 类型分组统计：排序后线性扫描分组
#pragma once

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include <algorithm>
#include "../common/sortstats.h"
#include "sortgen.h"

namespace ml {

/* ---------------- 开放寻址 FlatMap：movieId → 行号（25M 次查名单次扫描用） ---------------- */
class FlatMap {
public:
    void build(const std::vector<int>& keys) {
        cap_ = 1;
        while (cap_ < (int)keys.size() * 2) cap_ <<= 1;
        mask_ = cap_ - 1;
        slot_.assign(cap_, -1);
        for (int i = 0; i < (int)keys.size(); i++) {
            unsigned h = ((unsigned)keys[i] * 2654435761u) & mask_;
            while (slot_[h] != -1) h = (h + 1) & mask_;
            slot_[h] = i;
        }
        key_ = keys;
    }
    int find(int k) const {           /* 未命中返回 -1 */
        unsigned h = ((unsigned)k * 2654435761u) & mask_;
        for (;;) {
            int s = slot_[h];
            if (s == -1) return -1;
            if (key_[s] == k) return s;
            h = (h + 1) & mask_;
        }
    }
private:
    int cap_ = 0, mask_ = 0;
    std::vector<int> slot_, key_;
};

/* ---------------- 电影行 ---------------- */
struct MovieRow {
    int id = 0;
    int year = 0;
    int genreId = 0;
    std::string title;
    long long count = 0;      /* 评分条数（关注度） */
    long long sumIdx = 0;     /* ratingIdx 求和（0..9）；ridx = int(rating*2)-1 */
    /* rating = (ridx+1)/2 → mean = (avgIdx + 1)/2 */
    double mean() const { return count ? (sumIdx / (double)count + 1.0) / 2.0 : 0.0; }
};

struct SearchHit {
    const MovieRow* mv;
    double score;
};

/* ---------------- 数据仓库 ---------------- */
class Store {
public:
    std::vector<MovieRow> movies;            /* 按 movieId 升序 */
    std::vector<std::string> genreNames;     /* genreId → 名 */
    FlatMap id2slot;
    long long nPostings = 0;
    long long nUsers = 0;                    /* 以 (uid<<4) 高位估计：改为键扫描估计 */
    std::vector<std::pair<int, long long>> yearHist;   /* (年份, 评分条数) */
    std::vector<std::pair<std::string, long long>> genreHist; /* (类型, 评分条数) */
    long long maxUserId = 0;

    bool load(const std::string& datadir) {
        /* 1) movies.dat：id \t year \t primaryGenre \t title */
        std::FILE* f = std::fopen((datadir + "/movies.dat").c_str(), "rb");
        if (!f) return false;
        std::string line;
        int ch;
        auto getline = [&]() -> bool {
            line.clear();
            while ((ch = std::fgetc(f)) != EOF) {
                if (ch == '\n') {
                    if (!line.empty() && line.back() == '\r') line.pop_back();  /* Windows 文本行尾 */
                    return true;
                }
                line.push_back((char)ch);
            }
            return !line.empty();
        };
        std::vector<std::string> genreIndex;
        std::sort(genreIndex.begin(), genreIndex.end());
        while (getline()) {
            size_t p1 = line.find('\t');
            size_t p2 = (p1 == std::string::npos) ? p1 : line.find('\t', p1 + 1);
            size_t p3 = (p2 == std::string::npos) ? p2 : line.find('\t', p2 + 1);
            if (p3 == std::string::npos) continue;
            MovieRow m;
            m.id = std::atoi(line.substr(0, p1).c_str());
            m.year = std::atoi(line.substr(p1 + 1, p2 - p1 - 1).c_str());
            std::string g = line.substr(p2 + 1, p3 - p2 - 1);
            auto it = std::find(genreIndex.begin(), genreIndex.end(), g);
            if (it == genreIndex.end()) {
                genreIndex.push_back(g);
                m.genreId = (int)genreIndex.size() - 1;
            } else {
                m.genreId = (int)(it - genreIndex.begin());
            }
            m.title = line.substr(p3 + 1);
            movies.push_back(std::move(m));
        }
        std::fclose(f);
        genreNames = genreIndex;

        std::vector<int> ids;
        ids.reserve(movies.size());
        for (auto& m : movies) ids.push_back(m.id);
        id2slot.build(ids);

        /* 2) 流式扫描 postings 键文件：统计每部电影的 count/sumIdx 与直方图。
         *    不常驻 200MB 键数组 —— 键数组只在矩阵/外部排序模式按需加载。
         *    文件回退：无全量 postings.dat 时（转发包默认不含 200MB 全量数据）
         *    依次回退 1e6 / 1e5 抽样档，保证 UI 与统计功能开箱即用。 */
        const char* keyFiles[] = { "/postings.dat", "/postings_1e6.dat", "/postings_1e5.dat" };
        std::FILE* pf = nullptr;
        for (const char* kf : keyFiles) {
            pf = std::fopen((datadir + kf).c_str(), "rb");
            if (pf) break;
        }
        if (!pf) return false;
        const size_t BUFSZ = 1 << 20;   /* 1M 键/块 */
        std::vector<unsigned long long> buf(BUFSZ);
        std::vector<long long> yearCnt(2101, 0), genreCnt;
        genreCnt.assign(genreIndex.size(), 0);
        size_t r;
        while ((r = std::fread(buf.data(), 8, BUFSZ, pf)) > 0) {
            for (size_t i = 0; i < r; i++) {
                unsigned long long key = buf[i];
                int mid = (int)(key >> 22);
                long long ridx = (long long)(key & 15);
                int slot = id2slot.find(mid);
                if (slot < 0) continue;
                movies[slot].count++;
                movies[slot].sumIdx += ridx;
                genreCnt[movies[slot].genreId]++;
                if (movies[slot].year > 1900 && movies[slot].year <= 2100)
                    yearCnt[movies[slot].year]++;
                long long uid = (long long)((key >> 4) & 0x3FFFF);
                if (uid > maxUserId) maxUserId = uid;
                nPostings++;
            }
        }
        std::fclose(pf);
        for (int y = 1919; y <= 2100; y++)
            if (yearCnt[y] > 0) yearHist.push_back({y, yearCnt[y]});
        for (size_t g = 0; g < genreIndex.size(); g++)
            genreHist.push_back({genreIndex[g], genreCnt[g]});
        return true;
    }

    /* ---------------- 应用功能 1：影片检索（子串匹配 → 按关注度排序） ---------------- */
    std::vector<SearchHit> search(const std::string& q, int limit) const {
        std::vector<SearchHit> hits;
        if (q.empty()) return hits;
        std::string ql = lower(q);
        for (const auto& m : movies) {
            size_t pos = lower(m.title).find(ql);
            if (pos == std::string::npos) continue;
            double s = (double)m.count + (pos == 0 ? 1e9 : 0);   /* 前缀命中加权 */
            hits.push_back({&m, s});
        }
        topn_by_score(hits, limit);
        return hits;
    }

    /* 堆选择 Top-N：O(n log k)（应用功能 3 的底层，也是第 10 点 Top-K 对比的对象） */
    static void topn_by_score(std::vector<SearchHit>& hits, int k) {
        if ((int)hits.size() > k) {
            auto worse = [](const SearchHit& a, const SearchHit& b) {
                return a.score > b.score;    /* 小顶堆：堆顶 = 当前最差 */
            };
            std::make_heap(hits.begin(), hits.begin() + k, worse);
            for (size_t i = k; i < hits.size(); i++) {
                if (hits[i].score > hits[0].score) {
                    std::pop_heap(hits.begin(), hits.begin() + k, worse);
                    hits[k - 1] = hits[i];
                    std::push_heap(hits.begin(), hits.begin() + k, worse);
                }
            }
            hits.resize(k);
        }
        std::sort(hits.begin(), hits.end(),
                  [](const SearchHit& a, const SearchHit& b) { return a.score > b.score; });
    }

    /* ---------------- 应用功能 2：电影榜单（多级键编码 + 自实现排序） ----------------
     * 模式 pop：关注度↓ 为主键，好评度↓ 为次键 —— key = count<<8 | (63-avgX10)
     * 模式 rat：好评度↓ 为主键，关注度↓ 为次键 —— key = avgX10<<24 | min(count,2^24-1)
     * 模式 bayes：加权评分（IMDb 贝叶斯口径）—— 均分按票数向全局均分收缩：
     *   w = count/(count+m)·mean + m/(count+m)·globalMean，m = 全局票数中位数。
     *   只用均分排序时「2 票 5.0★」会压过「3000 票 4.4★」；加权评分把票数当作
     *   可信度权重，既保留"评分优先"的语义，又不被小样本拉偏。
     * 键编码把"多级复合键"问题转化为"单 int64 键"问题，直接复用第 3/4 点的排序算法；
     * 电影行号并入低 18 位保证键唯一，排序结果可逆解码。 */
    std::vector<const MovieRow*> rank_topk(int k, int genreId, long long minVotes,
                                           const char* mode) const {
        std::vector<long long> keys;
        std::vector<int> rows;
        bool bayes = (std::strcmp(mode, "bayes") == 0);
        double globalMean = 3.5, mPrior = 1.0;
        if (bayes) {
            /* 全局均分 + 票数中位数：作为收缩先验，一次线性扫描 */
            double sumAll = 0.0;
            long long cntAll = 0;
            std::vector<long long> cnts;
            cnts.reserve(movies.size());
            for (const MovieRow& m : movies) {
                if (m.count <= 0) continue;
                sumAll += m.sumIdx;
                cntAll += m.count;
                cnts.push_back(m.count);
            }
            if (cntAll) globalMean = (sumAll / (double)cntAll + 1.0) / 2.0;
            if (!cnts.empty()) {
                std::sort(cnts.begin(), cnts.end());
                mPrior = (double)cnts[cnts.size() / 2];
                if (mPrior < 1.0) mPrior = 1.0;
            }
        }
        for (int i = 0; i < (int)movies.size(); i++) {
            const MovieRow& m = movies[i];
            if (genreId >= 0 && m.genreId != genreId) continue;
            if (m.count < minVotes) continue;
            int avgX10 = (int)(m.mean() * 10.0 + 0.5);
            long long sk;
            if (bayes) {
                double w = m.count / (m.count + mPrior);
                int wx10 = (int)((w * m.mean() + (1.0 - w) * globalMean) * 10.0 + 0.5);
                if (wx10 < 0) wx10 = 0;
                if (wx10 > 50) wx10 = 50;
                sk = ((long long)wx10 << 24) | (long long)std::min<long long>(m.count, (1 << 24) - 1);
            }
            else if (std::strcmp(mode, "rat") == 0)
                sk = ((long long)avgX10 << 24) | (long long)std::min<long long>(m.count, (1 << 24) - 1);
            else
                sk = ((long long)std::min<long long>(m.count, (1LL << 40) - 1) << 8) | (long long)(63 - avgX10);
            keys.push_back((sk << 18) | (long long)i);   /* 行号并入低 18 位 */
            rows.push_back(i);
        }
        SortStats st;
        sg::quickSort<long long>(keys.data(), (int)keys.size(), 1, &st);  /* 升序 → 取末尾 k */
        std::vector<const MovieRow*> out;
        for (int i = (int)keys.size() - 1; i >= 0 && (int)out.size() < k; i--)
            out.push_back(&movies[(int)(keys[i] & ((1 << 18) - 1))]);
        return out;
    }

    /* ---------------- 应用功能 4：类型分组（排序后线性扫描） ---------------- */
    std::vector<std::pair<std::string, std::pair<long long, long long>>> group_by_genre() const {
        std::vector<int> order(movies.size());
        for (size_t i = 0; i < movies.size(); i++) order[i] = (int)i;
        std::sort(order.begin(), order.end(), [&](int a, int b) {
            return movies[a].genreId != movies[b].genreId
                 ? movies[a].genreId < movies[b].genreId
                 : movies[a].id < movies[b].id;
        });
        std::vector<std::pair<std::string, std::pair<long long, long long>>> out;
        int i = 0;
        while (i < (int)order.size()) {
            int g = movies[order[i]].genreId;
            long long cnt = 0, sum = 0;
            while (i < (int)order.size() && movies[order[i]].genreId == g) {
                cnt += movies[order[i]].count;
                sum += movies[order[i]].sumIdx;
                i++;
            }
            out.push_back({genreNames[g], {cnt, sum}});
        }
        return out;
    }

private:
    static std::string lower(const std::string& s) {
        std::string r = s;
        for (auto& c : r) if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
        return r;
    }
};

}  // namespace ml
