// entity_index.h —— 多场景通用实体层（多场景应用方案 1.1 数据契约的 C++ 端）
//
// 六个场景（movie/amazon/bike/web/flight/gene）共用一套应用逻辑：
//   实体台账 entities.dat (id/year/category/val/name) + posting 键 postings.dat，
//   键位布局由 meta.txt 的 attrBits/subBits 参数化：
//     attr = key & ((1<<attrBits)-1)            —— 低位属性（如评分档；可为 0 位）
//     sub  = (key>>attrBits) & ((1<<subBits)-1) —— 中位子键（如用户/分钟/目标）
//     entity = key >> (attrBits+subBits)        —— 高位实体 id
//   MovieLens: mid<<22|uid<<4|ridx (a=4,s=18)   Amazon: item<<26|user<<4|ridx (a=4,s=22)
//   CitiBike:  station<<21|minute (a=0,s=21)    Flight: origin<<22|minute (a=0,s=22)
//   WebGraph:  src<<20|dst (a=0,s=20)
// 排序职责与 movie 场景同源：倒排/台账聚合（键排序）、多级键榜单、检索 Top-N、
// 分类直方图。键文件自动回退 postings.dat → postings_1e6.dat → postings_1e5.dat。
#pragma once

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include <map>
#include <algorithm>
#include "../common/sortstats.h"
#include "sortgen.h"

namespace ed {

/* 键位布局（来自 meta.txt） */
struct KeyLayout {
    int attrBits = 0;
    int subBits = 0;
    unsigned long long attrMask() const { return attrBits ? ((1ULL << attrBits) - 1) : 0ULL; }
    unsigned long long subMask() const { return subBits ? ((1ULL << subBits) - 1) : 0ULL; }
    int entityShift() const { return attrBits + subBits; }
};

/* ---------------- 开放寻址 FlatMap：实体 id → 行号 ---------------- */
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
    int find(int k) const {
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

/* ---------------- 实体行 ---------------- */
struct EntityRow {
    int id = 0;
    int year = 0;
    int catId = 0;
    long long val = 0;        /* 静态数值属性（入度/价格/容量…，语义由 meta 定义） */
    std::string name;
    long long count = 0;      /* posting 条数（关注度） */
    long long sumAttr = 0;    /* attr 求和（attrBits=0 时恒 0） */
    /* mean() 由 EntityStore 的 meanScale/meanOffset 解释（attr 刻度按场景定义） */
    double mean(double scale, double offset) const {
        return count ? scale * (sumAttr / (double)count) + offset : 0.0;
    }
};

struct SearchHit {
    const EntityRow* ent;
    double score;
};

/* ---------------- 场景数据仓库 ---------------- */
class EntityStore {
public:
    std::string scn, name, domain;
    KeyLayout layout;
    std::string entityLabel = "实体", subLabel = "子键", attrLabel = "属性";
    std::string countLabel = "计数", valLabel = "数值";
    double meanScale = 0.5, meanOffset = 0.25;   /* 均分换算：mean = scale*avg(attr)+offset */
    std::vector<EntityRow> ents;
    std::vector<std::string> catNames;
    FlatMap id2slot;
    long long nPostings = 0;
    long long maxSub = 0;
    std::vector<std::pair<int, long long>> yearHist;
    std::vector<std::pair<std::string, long long>> catHist;

    /* meta.txt: key=value 行（值为 JSON 时只取标量字段） */
    static std::map<std::string, std::string> readMeta(const std::string& path) {
        std::map<std::string, std::string> m;
        std::FILE* f = std::fopen(path.c_str(), "rb");
        if (!f) return m;
        std::string line;
        int ch;
        while ((ch = std::fgetc(f)) != EOF) {
            if (ch == '\n') {
                size_t eq = line.find('=');
                if (eq != std::string::npos)
                    m[line.substr(0, eq)] = line.substr(eq + 1);
                line.clear();
            } else if (ch != '\r') line.push_back((char)ch);
        }
        std::fclose(f);
        return m;
    }

    bool load(const std::string& datadir) {
        auto meta = readMeta(datadir + "/meta.txt");
        if (meta.empty()) return false;
        scn = meta.count("scn") ? meta["scn"] : "scn";
        name = meta.count("name") ? meta["name"] : scn;
        if (meta.count("domain")) domain = meta["domain"];
        if (!meta.count("attrBits") || !meta.count("subBits")) return false;
        layout.attrBits = std::atoi(meta["attrBits"].c_str());
        layout.subBits = std::atoi(meta["subBits"].c_str());
        if (meta.count("entityLabel")) entityLabel = meta["entityLabel"];
        if (meta.count("subLabel")) subLabel = meta["subLabel"];
        if (meta.count("attrLabel")) attrLabel = meta["attrLabel"];
        if (meta.count("countLabel")) countLabel = meta["countLabel"];
        if (meta.count("meanScale")) meanScale = std::atof(meta["meanScale"].c_str());
        if (meta.count("meanOffset")) meanOffset = std::atof(meta["meanOffset"].c_str());
        if (meta.count("valLabel")) valLabel = meta["valLabel"];

        /* 1) entities.dat: id \t year \t category \t val \t name */
        std::FILE* f = std::fopen((datadir + "/entities.dat").c_str(), "rb");
        if (!f) return false;
        std::string line;
        int ch;
        auto getline = [&]() -> bool {
            line.clear();
            while ((ch = std::fgetc(f)) != EOF) {
                if (ch == '\n') {
                    if (!line.empty() && line.back() == '\r') line.pop_back();
                    return true;
                }
                line.push_back((char)ch);
            }
            return !line.empty();
        };
        std::vector<std::string> catIndex;
        while (getline()) {
            size_t p1 = line.find('\t');
            size_t p2 = (p1 == std::string::npos) ? p1 : line.find('\t', p1 + 1);
            size_t p3 = (p2 == std::string::npos) ? p2 : line.find('\t', p2 + 1);
            size_t p4 = (p3 == std::string::npos) ? p3 : line.find('\t', p3 + 1);
            if (p4 == std::string::npos) continue;
            EntityRow e;
            e.id = std::atoi(line.substr(0, p1).c_str());
            e.year = std::atoi(line.substr(p1 + 1, p2 - p1 - 1).c_str());
            std::string cat = line.substr(p2 + 1, p3 - p2 - 1);
            auto it = std::find(catIndex.begin(), catIndex.end(), cat);
            if (it == catIndex.end()) { catIndex.push_back(cat); e.catId = (int)catIndex.size() - 1; }
            else e.catId = (int)(it - catIndex.begin());
            e.val = std::atoll(line.substr(p3 + 1, p4 - p3 - 1).c_str());
            e.name = line.substr(p4 + 1);
            ents.push_back(std::move(e));
        }
        std::fclose(f);
        catNames = catIndex;
        if (ents.empty()) return false;   /* 空台账（如数据未到齐）不作为可用场景 */

        std::vector<int> ids;
        ids.reserve(ents.size());
        for (auto& e : ents) ids.push_back(e.id);
        id2slot.build(ids);

        /* 2) 流式扫描键文件（带回退链），按布局解码并聚合 */
        const char* keyFiles[] = { "/postings.dat", "/postings_1e6.dat", "/postings_1e5.dat" };
        std::FILE* pf = nullptr;
        for (const char* kf : keyFiles) {
            pf = std::fopen((datadir + kf).c_str(), "rb");
            if (pf) break;
        }
        if (!pf) return false;
        const size_t BUFSZ = 1 << 20;
        std::vector<unsigned long long> buf(BUFSZ);
        std::vector<long long> yearCnt(2101, 0), catCnt(catIndex.size(), 0);
        const unsigned long long am = layout.attrMask(), sm = layout.subMask();
        const int esh = layout.entityShift(), ab = layout.attrBits;
        size_t r;
        while ((r = std::fread(buf.data(), 8, BUFSZ, pf)) > 0) {
            for (size_t i = 0; i < r; i++) {
                unsigned long long key = buf[i];
                long long attr = ab ? (long long)(key & am) : 0;
                long long sub = (long long)((key >> ab) & sm);
                int slot = id2slot.find((int)(key >> esh));
                if (slot < 0) continue;
                ents[slot].count++;
                ents[slot].sumAttr += attr;
                catCnt[ents[slot].catId]++;
                if (ents[slot].year > 1900 && ents[slot].year <= 2100)
                    yearCnt[ents[slot].year]++;
                if (sub > maxSub) maxSub = sub;
                nPostings++;
            }
        }
        std::fclose(pf);
        for (int y = 1901; y <= 2100; y++)
            if (yearCnt[y] > 0) yearHist.push_back({y, yearCnt[y]});
        for (size_t c = 0; c < catIndex.size(); c++)
            catHist.push_back({catIndex[c], catCnt[c]});
        return true;
    }

    double meanOf(const EntityRow& e) const { return e.mean(meanScale, meanOffset); }

    /* ---------------- 检索 Top-N（名称子串 → 关注度打分 → 堆选择） ---------------- */
    std::vector<SearchHit> search(const std::string& q, int limit) const {
        std::vector<SearchHit> hits;
        if (q.empty()) return hits;
        std::string ql = lower(q);
        for (const auto& e : ents) {
            size_t pos = lower(e.name).find(ql);
            if (pos == std::string::npos) continue;
            double s = (double)e.count + (pos == 0 ? 1e9 : 0);
            hits.push_back({&e, s});
        }
        topn_by_score(hits, limit);
        return hits;
    }

    static void topn_by_score(std::vector<SearchHit>& hits, int k) {
        if ((int)hits.size() > k) {
            auto worse = [](const SearchHit& a, const SearchHit& b) { return a.score > b.score; };
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

    /* ---------------- 通用多级键榜单（排序依据由 mode 决定） ----------------
     * pop: 关注度↓ → 均分↓   key = min(count,2^31-1)<<6 | (63-avgX10)
     * rat: 均分↓ → 关注度↓   key = avgX10<<34 | min(count,2^34-1)   （需 attrBits>0）
     * val: 静态数值↓（单键）  key = min(val,2^42-1)
     * 行号并入低 20 位（≤1,048,576 个实体）保证键唯一、可逆解码。 */
    std::vector<const EntityRow*> rank_topk(int k, int catId, long long minCount,
                                            const char* mode) const {
        std::vector<long long> keys;
        std::string m = mode;
        for (int i = 0; i < (int)ents.size(); i++) {
            const EntityRow& e = ents[i];
            if (catId >= 0 && e.catId != catId) continue;
            if (e.count < minCount) continue;
            int avgX10 = (int)(meanOf(e) * 10.0 + 0.5);
            long long sk;
            if (m == "rat")
                sk = ((long long)avgX10 << 34) | (long long)std::min<long long>(e.count, (1LL << 34) - 1);
            else if (m == "val")
                sk = (long long)std::min<long long>(e.val, (1LL << 42) - 1);
            else
                sk = ((long long)std::min<long long>(e.count, (1LL << 31) - 1) << 6) | (long long)(63 - avgX10);
            keys.push_back((sk << 20) | (long long)i);
        }
        SortStats st;
        sg::quickSort<long long>(keys.data(), (int)keys.size(), 1, &st);
        std::vector<const EntityRow*> out;
        for (long long i = (long long)keys.size() - 1; i >= 0 && (int)out.size() < k; i--)
            out.push_back(&ents[(int)(keys[(size_t)i] & ((1 << 20) - 1))]);
        return out;
    }

    /* runTopK 用：与 rank_topk 同编码的"候选得分键"数组（脱离实体表做纯键对比） */
    void scoreKeys(std::vector<long long>& out, const char* mode) const {
        std::string m = mode;
        for (int i = 0; i < (int)ents.size(); i++) {
            const EntityRow& e = ents[i];
            int avgX10 = (int)(meanOf(e) * 10.0 + 0.5);
            long long sk;
            if (m == "rat")
                sk = ((long long)avgX10 << 34) | (long long)std::min<long long>(e.count, (1LL << 34) - 1);
            else if (m == "val")
                sk = (long long)std::min<long long>(e.val, (1LL << 42) - 1);
            else
                sk = ((long long)std::min<long long>(e.count, (1LL << 31) - 1) << 6) | (long long)(63 - avgX10);
            out.push_back(sk);
        }
    }

private:
    static std::string lower(const std::string& s) {
        std::string r = s;
        for (auto& c : r) if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
        return r;
    }
};

}  // namespace ed
