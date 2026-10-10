#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_build_region_map.py —— 生成「影片 → 制片地区分组」映射（region_map.json）。

为什么需要地区映射
------------------------------------------------------------------
movie 场景的底料 MovieLens 32M 是**北美（DVD 租借/流媒体）用户**的评分行为，
非英语片的评分条数天然比好莱坞经典低一到两个数量级。实测本数据集：

    Crouching Tiger, Hidden Dragon   3.3 万票  → 关注度 #114（能看见）
    In the Mood for Love             4 千票    → 关注度 #1735（排不进前面）
    Infernal Affairs                 3 千票    → 关注度 #2xxx

也就是说「榜单里全是欧美片」不是代码过滤掉了谁，而是**票数结构**决定的。
要让华语/日韩/南亚等影片「可见」，正确做法是提供一个**地区筛选维度**——
它们本来就在库里，只是排不进总榜。

判定口径（为什么用「原语言」而不是「制片国家」）
------------------------------------------------------------------
只用 P495（制片国家/地区）会把大量欧美合拍片误判成亚洲片：
《Blade Runner》《Looper》《Lost in Translation》都因为「有香港/日本制片方」
被标成华语/日韩。实测这批误判会让「华语榜」首页出现好莱坞片。

因此本脚本以 **P364 原语言**（original language of film）为主判据——
它描述的是「这部电影本身是什么语言的作品」，对合拍片的判定符合观众直觉：
《Blade Runner》原语言是英语（尽管有香港制片方），《In the Mood for Love》
原语言是粤语。

**并且要求英文不在原语言集合里**：Wikidata 上不少好莱坞片（《Serenity》《2012》
《Mission: Impossible III》）会挂上一串「配音语言」性质的 P364 值，其中包含中文，
于是被误判成华语片。实测「华语且含英语」仅 61 部，而「华语且不含英语」有 1926 部——
加一条 `FILTER NOT EXISTS { ?f wdt:P364 wd:Q1860 }` 就能把这类噪音清掉，
代价是漏掉极少数中英双语合拍片（可接受）。P495 仅作为语言缺失时的兜底。

用法：
    python app/pre/_build_region_map.py            # 直接写 app/data/region_map.json
    python app/pre/_build_region_map.py --out x.json
    python app/pre/_build_region_map.py --no-en-exclude   # 关闭英文排除（对比用）
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # app/
PROJ = os.path.dirname(ROOT)
DEF_OUT = os.path.join(ROOT, "data", "region_map.json")
SPARQL = "https://query.wikidata.org/sparql"
UA = {
    "User-Agent": ("CineRank-course/1.0 (algorithm course project; "
                   "region map; contact: cinerank-course@example.invalid) "
                   "python-urllib/3"),
    "Accept": "application/sparql-results+json",
}
CTX = ssl.create_default_context()

# 原语言（P364）→ 地区分组。
# 分组偏「观众认知」而非语言学分类：华语区合并，日韩合并，南亚合并，
# 东南亚合并，西亚·中亚合并——这样下拉框才有可用的粒度。
LANG_GROUP = {
    # 华语（含各汉语变体）
    "Q7850": "华语", "Q9186": "华语", "Q3129427": "华语", "Q13214": "华语",
    "Q36759": "华语", "Q36778": "华语", "Q13307": "华语", "Q56504": "华语",
    "Q727694": "华语",
    # 日韩
    "Q5287": "日韩", "Q9176": "日韩",
    # 南亚
    "Q1568": "南亚", "Q36236": "南亚", "Q9610": "南亚", "Q13267": "南亚",
    "Q11051": "南亚", "Q5885": "南亚", "Q33268": "南亚", "Q33577": "南亚",
    "Q178559": "南亚", "Q13206": "南亚",
    # 东南亚
    "Q9217": "东南亚", "Q13216": "东南亚", "Q9288": "东南亚",
    "Q1860x": "东南亚", "Q36214": "东南亚", "Q36649": "东南亚",
    "Q33234": "东南亚", "Q33170": "东南亚", "Q33424": "东南亚",
    # 西亚·中亚
    "Q9168": "西亚·中亚", "Q256": "西亚·中亚", "Q150": "西亚·中亚",
    "Q33526": "西亚·中亚", "Q9252": "西亚·中亚", "Q9264": "西亚·中亚",
    "Q7737": "东欧·俄罗斯",
    # 拉美
    "Q1321": "拉美", "Q5146": "拉美",
    # 东欧·俄罗斯
    "Q7918": "东欧·俄罗斯", "Q9067": "东欧·俄罗斯", "Q36510": "东欧·俄罗斯",
    # 非洲
    "Q2955": "非洲", "Q35306": "非洲", "Q36301": "非洲",
}

# 语言缺失时的兜底：制片国家（P495）→ 分组。
# 只用于语言字段为空的影片，且刻意保守（宁可不标也不误标）。
COUNTRY_GROUP = {
    "Q148": "华语", "Q8646": "华语", "Q865": "华语", "Q14773": "华语",
    "Q334": "华语",
    "Q884": "日韩", "Q17": "日韩",
    "Q668": "南亚", "Q869": "东南亚", "Q881": "东南亚",
    "Q928": "东南亚", "Q833": "东南亚", "Q252": "东南亚",
    "Q794": "西亚·中亚", "Q43": "西亚·中亚",
    "Q155": "拉美", "Q96": "拉美", "Q414": "拉美",
    "Q159": "东欧·俄罗斯", "Q36": "东欧·俄罗斯", "Q212": "东欧·俄罗斯",
    "Q1033": "非洲",
}


def fetch(url, timeout=120, retries=4):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read()
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2.5 * (i + 1))


def sparql(query):
    u = SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    return json.loads(fetch(u).decode("utf-8"))


def ml_links():
    p = os.path.join(PROJ, "downloads", "ml-32m", "links.csv")
    if not os.path.exists(p):
        return {}
    out = {}
    for ln in open(p, encoding="utf-8"):
        c = ln.rstrip("\n").split(",")
        if len(c) >= 2 and c[1].isdigit():
            out["tt" + c[1].zfill(7)] = int(c[0])
    return out


def qid_set(qids):
    return " ".join(f"wd:{q}" for q in qids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEF_OUT)
    ap.add_argument("--no-en-exclude", action="store_true",
                    help="不排除「原语言含英语」的作品（默认排除，见文件头说明）")
    a = ap.parse_args()

    links = ml_links()
    print(f"MovieLens 映射 {len(links):,} 条")
    region = {}          # imdb -> set(groups)
    # 非英语原语言的作品才算「非欧美」；排除含英语者以滤掉配音语言噪音
    en_filter = ("" if a.no_en_exclude else
                 "  FILTER NOT EXISTS { ?f wdt:P364 wd:Q1860 }\n")

    # ---------- 主判据：原语言 ----------
    print(f"[1/2] 按原语言（P364）抓取…{'（已排除含英语者）' if en_filter else ''}")
    for qid, grp in LANG_GROUP.items():
        if qid.endswith("x"):
            continue
        q = f"""SELECT DISTINCT ?imdb WHERE {{
  ?f wdt:P31/wdt:P279* wd:Q11424 ; wdt:P364 wd:{qid} ; wdt:P345 ?imdb .
{en_filter}}}"""
        try:
            d = sparql(q)
        except Exception as e:
            print(f"      [warn] {grp}/{qid} 失败：{type(e).__name__}")
            time.sleep(1.0)
            continue
        n = 0
        for b in d.get("results", {}).get("bindings", []):
            im = (b.get("imdb") or {}).get("value", "")
            if im.startswith("tt"):
                region.setdefault(im, set()).add(grp)
                n += 1
        print(f"      {grp:10} {qid:10} +{n:>6}", flush=True)
        time.sleep(0.3)

    # ---------- 兜底：制片国家（仅补语言缺失者） ----------
    print("[2/2] 语言缺失的用制片国家（P495）兜底…")
    for qid, grp in COUNTRY_GROUP.items():
        q = f"""SELECT DISTINCT ?imdb WHERE {{
  ?f wdt:P31/wdt:P279* wd:Q11424 ; wdt:P495 wd:{qid} ; wdt:P345 ?imdb .
  FILTER NOT EXISTS {{ ?f wdt:P364 ?anyLang }}
}}"""
        try:
            d = sparql(q)
        except Exception as e:
            print(f"      [warn] {grp}/{qid} 失败：{type(e).__name__}")
            time.sleep(1.0)
            continue
        n = 0
        for b in d.get("results", {}).get("bindings", []):
            im = (b.get("imdb") or {}).get("value", "")
            if im.startswith("tt"):
                region.setdefault(im, set()).add(grp)
                n += 1
        if n:
            print(f"      {grp:10} {qid:10} +{n:>6}（兜底）", flush=True)
        time.sleep(0.3)

    # ---------- 只保留 MovieLens 里真有的影片 ----------
    out = {}
    for im, groups in region.items():
        mid = links.get(im)
        if mid is not None:
            out[str(mid)] = sorted(groups)
    print(f"\nMovieLens 可映射的非欧美影片：{len(out)} 部")
    import collections
    cc = collections.Counter()
    for v in out.values():
        for g in v:
            cc[g] += 1
    print("分组分布：", dict(cc))

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w"), ensure_ascii=False)
    print(f"写出 → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
