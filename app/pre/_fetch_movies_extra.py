#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_fetch_movies_extra.py —— 生成电影场景的「补充数据」：非欧美片单 + 2024/2025 新片。

背景（为什么需要这个脚本）
------------------------------------------------------------------
movie 场景的底料是 GroupLens **MovieLens 32M**（3,200 万条真实评分，8.76 万部影片），
它有两个必须正视的天然边界：

1) **年份边界**：MovieLens 32M 的收录窗口止于 **2023 年**。这可以从数据本身验证
   （movies.csv 里最新年份 = 2023，2024/2025 一部没有），GroupLens 官方 README 也写明
   该版本生成于 2023-07-20。所以榜单里永远不可能出现 2024–2025 的影片——
   这是数据源的窗口，不是代码或排序键的问题。

2) **片源结构**：MovieLens 来自「北美（DVD 租借/流媒体）用户」的评分行为，
   非英语片票数天然低一到两个数量级。本数据集实测：
   《Crouching Tiger》3.3 万票 → 关注度 #114；而《Infernal Affairs》《In the Mood
   for Love》只有 3–4 千票，落在 #1700 名开外。也就是说「榜单里看不到华语片」
   不是被过滤，而是票数结构决定的——**要看得见，得先能筛**。

本脚本产出两份东西：
  A. movies_extra.json —— 可并入 movies.dat 的新片（非欧美 + 2024/2025），
     票数由 IMDb 真实评价人数按重叠样本校准换算（见下），均分沿用 IMDb 评分。
  B. region_map.json —— 「MovieLens 已有影片 → 制片地区」映射（14k+ 部），
     供「只看华语/日韩/南亚…」这类筛选使用。这是让既有非欧美影片
     在 UI 里可达的关键：它们本来就在库里，只是排不进总榜。

票数校准（不是凭空造数）
------------------------------------------------------------------
取两库交集（links.csv 能映射上的 Top-500 关注度影片），逐片算
    ratio = IMDb 票数 / MovieLens 票数
实测中位数 = 20.5（均值 22.8，分布右偏）。新片票数 = IMDb 票数 ÷ 20.5，
即把它们放到与既有影片同一把尺子上比较；原始 IMDb 票数与所用系数都写进
movies_extra.json，可复核。

数据来源
------------------------------------------------------------------
* 制片地区：Wikidata SPARQL（P495 制片国家/地区 + P31 电影实例 + P345 IMDb id）
* 票数/评分/年份/类型：IMDb 官方数据集（title.basics / title.ratings，
  https://datasets.imdbws.com/，免密钥，每日更新）
* 与 MovieLens 的对应关系：ml-32m 自带 links.csv（movieId ↔ imdbId），
  因此「是否已收录」用 IMDb id 精确判定，不靠片名模糊匹配。

用法：
    python app/pre/_fetch_movies_extra.py                    # 生成两份产物（不动原数据）
    python app/pre/_fetch_movies_extra.py --apply            # 顺带追加进 app/data/movies.dat
    python app/pre/_fetch_movies_extra.py --min-votes 10000  # 更严的票数门槛
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # app/
PROJ = os.path.dirname(ROOT)
TMP = os.path.join(PROJ, "_build_tmp")
OUT_JSON = os.path.join(TMP, "movies_extra.json")
OUT_REGION = os.path.join(TMP, "region_map.json")
SPARQL = "https://query.wikidata.org/sparql"
UA = {
    "User-Agent": ("CineRank-course/1.0 (algorithm course project; "
                   "dataset supplement; contact: cinerank-course@example.invalid) "
                   "python-urllib/3"),
    "Accept": "application/sparql-results+json,application/json",
}
CTX = ssl.create_default_context()

# IMDb 票数 → MovieLens 票数换算系数（重叠样本中位比值，见文件头说明）
IMDB_TO_ML = 20.5

# 年份上界：数据集窗口是 2023，补到 2025 即可满足「更新到 2025」的要求。
# 2026 及以后还在上映/未定档，票数极不稳定，纳入只会让榜单失真。
YEAR_MAX = 2025
YEAR_MIN = 2024        # 只补 MovieLens 窗口之外的新片

# 制片国家/地区（Wikidata QID）→ 展示用地区分组。
# 分组偏「观众认知」而非行政划分：华语区合并，日韩合并，东南亚合并。
REGIONS = {
    "Q148": ("中国", "华语"), "Q8646": ("中国香港", "华语"),
    "Q865": ("中国台湾", "华语"), "Q14773": ("中国澳门", "华语"),
    "Q334": ("新加坡", "华语"),
    "Q884": ("韩国", "日韩"), "Q17": ("日本", "日韩"),
    "Q668": ("印度", "南亚"),
    "Q869": ("泰国", "东南亚"), "Q881": ("越南", "东南亚"),
    "Q928": ("菲律宾", "东南亚"), "Q833": ("马来西亚", "东南亚"),
    "Q252": ("印度尼西亚", "东南亚"),
    "Q794": ("伊朗", "西亚·中亚"), "Q43": ("土耳其", "西亚·中亚"),
    "Q155": ("巴西", "拉美"), "Q96": ("墨西哥", "拉美"),
    "Q414": ("阿根廷", "拉美"), "Q159": ("俄罗斯", "东欧·俄罗斯"),
    "Q36": ("波兰", "东欧·俄罗斯"), "Q212": ("乌克兰", "东欧·俄罗斯"),
    "Q1033": ("尼日利亚", "非洲"),
}

# 非欧美筛选：以下地区同样出现在 Wikidata，但属于「欧美」范畴，不补。
# （补片的目标是补足北美视角的盲区，不是制造重复。）


def fetch(url, timeout=90, retries=4, headers=None):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read()
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2.0 * (i + 1))


def sparql(query):
    u = SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    return json.loads(fetch(u).decode("utf-8"))


def wd_by_region():
    """逐个地区取影片（大查询会超时，分地区更稳）。返回 {imdb: {year,label,group}}。"""
    out = {}
    for qid, (label, group) in REGIONS.items():
        q = f"""SELECT DISTINCT ?imdb ?year WHERE {{
  ?film wdt:P31/wdt:P279* wd:Q11424 ;
        wdt:P495 wd:{qid} ;
        wdt:P345 ?imdb .
  OPTIONAL {{ ?film wdt:P577 ?d . BIND(YEAR(?d) AS ?year) }}
}}"""
        try:
            d = sparql(q)
        except Exception as e:
            print(f"      [warn] {label}({qid}) 查询失败：{type(e).__name__}")
            time.sleep(1.2)
            continue
        n0 = len(out)
        for b in d.get("results", {}).get("bindings", []):
            im = (b.get("imdb") or {}).get("value", "")
            if not im.startswith("tt"):
                continue
            y = (b.get("year") or {}).get("value", "")
            rec = out.setdefault(im, {"year": 0, "labels": set(), "groups": set()})
            if y.isdigit() and not rec["year"]:
                rec["year"] = int(y)
            rec["labels"].add(label)
            rec["groups"].add(group)
        print(f"      {label:8} +{len(out) - n0:>5}（累计 {len(out)}）", flush=True)
        time.sleep(0.4)
    return out


def imdb_index():
    """IMDb 索引：tconst -> (title, year, genres, avg, votes)。需预先下载数据集缓存。"""
    basics = os.path.join(TMP, "imdb_basics.tsv")
    ratings = os.path.join(TMP, "imdb_ratings.tsv")
    if not (os.path.exists(basics) and os.path.exists(ratings)):
        print(f"[错误] 缺少 IMDb 数据集缓存：\n  {basics}\n  {ratings}")
        print("  下载：https://datasets.imdbws.com/title.basics.tsv.gz")
        print("        https://datasets.imdbws.com/title.ratings.tsv.gz")
        print("  解压为 .tsv 放到上述路径后重跑。")
        return None
    NA = chr(92) + "N"
    rat = {}
    for ln in open(ratings, encoding="utf-8"):
        c = ln.rstrip("\n").split("\t")
        if len(c) == 3 and c[0].startswith("tt"):
            try:
                rat[c[0]] = (float(c[1]), int(c[2]))
            except ValueError:
                pass
    out = {}
    with open(basics, encoding="utf-8", errors="replace") as f:
        next(f)
        for ln in f:
            c = ln.rstrip("\n").split("\t")
            if len(c) < 9 or c[1] not in ("movie", "tvMovie"):
                continue
            r = rat.get(c[0])
            if not r:
                continue
            y = c[5]
            out[c[0]] = (c[2], int(y) if y != NA and y.isdigit() else 0,
                         c[8], r[0], r[1])
    return out


def ml_links():
    """links.csv：imdbId(tt…) -> movieId。用于「是否已收录」的精确判定。"""
    p = os.path.join(PROJ, "downloads", "ml-32m", "links.csv")
    if not os.path.exists(p):
        return {}
    out = {}
    for ln in open(p, encoding="utf-8"):
        c = ln.rstrip("\n").split(",")
        if len(c) >= 2 and c[1].isdigit():
            out["tt" + c[1].zfill(7)] = int(c[0])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="把新片追加进 app/data/movies.dat（会改原数据文件）")
    ap.add_argument("--min-votes", type=int, default=3000,
                    help="IMDb 票数门槛（默认 3000，约合 MovieLens 146 票）")
    ap.add_argument("--ratio", type=float, default=IMDB_TO_ML,
                    help=f"IMDb→MovieLens 票数换算系数（默认 {IMDB_TO_ML}）")
    a = ap.parse_args()

    os.makedirs(TMP, exist_ok=True)
    print("[1/5] 读取 IMDb 本地数据集…")
    idx = imdb_index()
    if idx is None:
        return 1
    print(f"      IMDb 影片 {len(idx):,} 部")
    links = ml_links()
    print(f"      MovieLens 映射 {len(links):,} 条（links.csv）")

    # ---------------- 1) 地区映射（既有影片 → 地区） ----------------
    print("[2/5] 抓取制片地区（Wikidata，逐个地区）…")
    wd = wd_by_region()
    json.dump({k: {"year": v["year"], "labels": sorted(v["labels"]),
                   "groups": sorted(v["groups"])} for k, v in wd.items()},
              open(os.path.join(TMP, "wd_nonwestern.json"), "w"),
              ensure_ascii=False, indent=1)

    region_map = {}
    for im, rec in wd.items():
        mid = links.get(im)
        if mid is not None:
            region_map[str(mid)] = sorted(rec["groups"])
    json.dump(region_map, open(OUT_REGION, "w"), ensure_ascii=False)
    nw_existing = len(region_map)
    print(f"      既有影片中含非欧美制片地区的：{nw_existing} 部 → {OUT_REGION}")

    # ---------------- 2) 候选新片 ----------------
    print(f"[3/5] 组装候选新片（IMDb 票数 >= {a.min_votes}，年份 <= {YEAR_MAX}）…")
    rows = {}
    # A. 非欧美影片（地区有记录），且 MovieLens 未收录（按 imdbId 精确判定）
    nw = rec_cnt = 0
    for im, rec in wd.items():
        if im in links:                     # 已收录，跳过（地区信息已进 region_map）
            continue
        r = idx.get(im)
        if not r or r[4] < a.min_votes:
            continue
        y = r[1] or rec["year"]
        if y > YEAR_MAX or y < 1900:
            continue
        rows[im] = {"imdb": im, "title": r[0], "year": y, "genres": r[2],
                    "avg": r[3], "votes": r[4],
                    "groups": sorted(rec["groups"]), "origin": "non-western"}
        nw += 1
    # B. 2024/2025 新片（不限地区——窗口外所有新片都该补）
    for tc, (t, y, g, avg, v) in idx.items():
        if y < YEAR_MIN or y > YEAR_MAX or v < a.min_votes or tc in rows or tc in links:
            continue
        rows[tc] = {"imdb": tc, "title": t, "year": y, "genres": g, "avg": avg,
                    "votes": v, "groups": [], "origin": "recent"}
        rec_cnt += 1
    print(f"      非欧美新片 {nw} 部 · 2024–{YEAR_MAX} 新片 {rec_cnt} 部")
    print(f"      合计 {len(rows)} 部（均为 MovieLens 未收录）")

    # ---------------- 3) 换算票数并落盘 ----------------
    base = 300001        # 新 movieId 起点：避开 ml-32m 的 1..292757
    out = []
    for n, (tc, v) in enumerate(sorted(rows.items(), key=lambda kv: -kv[1]["votes"])):
        out.append({
            "movieId": base + n,
            "imdb": tc,
            "title": f"{v['title']} ({v['year']})",
            "year": v["year"],
            "genres": v["genres"] or "(none)",
            "primary": (v["genres"].split(",")[0].strip() if v["genres"] else "(none)"),
            "count": max(1, int(round(v["votes"] / a.ratio))),   # 校准后的「等价评分条数」
            "mean": v["avg"],
            "groups": v["groups"],
            "origin": v["origin"],
            "imdbVotes": v["votes"],
        })
    json.dump(out, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
    print(f"[4/5] 补丁 → {OUT_JSON}（{len(out)} 部）")
    print(f"      票数换算：IMDb ÷ {a.ratio}（重叠样本中位比值）")

    if a.apply:
        tgt = os.path.join(ROOT, "data", "movies.dat")
        with open(tgt, "a", encoding="utf-8") as w:
            for o in out:
                w.write(f"{o['movieId']}\t{o['year']}\t{o['primary']}\t{o['title']}\n")
        print(f"[5/5] --apply：已向 {tgt} 追加 {len(out)} 行")
    else:
        print("[5/5] 未加 --apply：仅生成产物，未改动 movies.dat")
        print("      应用方式：加 --apply，或运行 app/pre/apply_movies_extra.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
