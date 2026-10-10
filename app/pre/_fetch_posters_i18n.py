#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_fetch_posters_i18n.py —— 用「原语言片名」补冷门非英语片的海报。

为什么单独一个脚本
------------------------------------------------------------------
`_fetch_posters.py` 走英文维基（pageimages / opensearch / REST）。但榜单里
（尤其「好评度」排序）会冒出大量非英语地区作品，它们在英文维基根本没有条目：

    Atatürk II 1881–1919      英文无条目、土耳其语有
    Kesari Chapter 2          英文无条目、印地语有
    Demon Slayer 无限城篇      英文条目名与片名差很远、日文条目直接命中

这些片的**原语言片名**在 IMDb 的 akas 数据集里（title.akas.tsv 的
original title 字段）已经拿到，所以本脚本做最后一段：原语言片名 → 对应语言
维基 → 取图。命中率比在英文维基硬碰硬高得多。

前置条件（都已由前面的流程产生）：
    _build_tmp/imdb_akas.tsv        IMDb alternative titles
    _build_tmp/miss_all.json        待补 movieId 列表
    app/data/movies.dat             id → 片名/年份
    downloads/ml-32m/links.csv      movieId ↔ imdbId

用法：
    python app/pre/_fetch_posters_i18n.py                 # 补 miss_all.json 里的
    python app/pre/_fetch_posters_i18n.py --ids a,b,c     # 指定 movieId
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # app/
PROJ = os.path.dirname(ROOT)
TMP = os.path.join(PROJ, "_build_tmp")
GET = os.path.join(ROOT, "frontend", "assets", "posters")
CACHE = os.path.join(TMP, "poster_resolve_cache.json")
AKAS = os.path.join(TMP, "imdb_akas.tsv")
LINKS = os.path.join(PROJ, "downloads", "ml-32m", "links.csv")

# IMDb 语言码 → 维基子域
LANGMAP = {
    "cmn": "zh", "yue": "zh", "zh": "zh", "tr": "tr", "hi": "hi", "ja": "ja",
    "te": "te", "ml": "ml", "ta": "ta", "id": "id", "ko": "ko", "fa": "fa",
    "de": "de", "fr": "fr", "es": "es", "it": "it", "ru": "ru", "pt": "pt",
    "pl": "pl", "cs": "cs", "th": "th", "vi": "vi", "ar": "ar", "he": "he",
    "el": "el", "bn": "bn", "mr": "mr", "kn": "kn", "pa": "pa", "ur": "ur",
    "sv": "sv", "no": "no", "da": "da", "fi": "fi", "nl": "nl", "hu": "hu",
    "ro": "ro", "uk": "uk", "sr": "sr", "hr": "hr", "bg": "bg", "sk": "sk",
}
NONE = chr(92) + "N"


def load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fp", os.path.join(ROOT, "pre", "_fetch_posters.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# 按文字特征猜语种（akas 的 lang 字段经常是 \N，只能靠字形）
def guess_lang(name):
    import re
    if not name:
        return None
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", name):
        # 含假名 → 日语；纯汉字 → 中文
        return "ja" if re.search(r"[\u3040-\u30ff]", name) else "cmn"
    if re.search(r"[\u0900-\u097f]", name):
        return "hi"          # 天城文
    if re.search(r"[\u0c00-\u0c7f]", name):
        return "te"          # 泰卢固文
    if re.search(r"[\u0b80-\u0bff]", name):
        return "ta"          # 泰米尔文
    if re.search(r"[\u0d00-\u0d7f]", name):
        return "ml"          # 马拉雅拉姆文
    if re.search(r"[\u0600-\u06ff]", name):
        return "fa"          # 阿拉伯字母（波斯/乌尔都）
    if re.search(r"[\uac00-\ud7af]", name):
        return "ko"
    if re.search(r"[ığşçöüİĞŞÇÖÜ]", name):
        return "tr"          # 土耳其语特有字母
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="逗号分隔的 movieId；默认读 miss_all.json")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 部（0=不限）")
    ap.add_argument("--proxy", default=os.environ.get("CINERANK_PROXY", ""),
                    help="HTTP 代理，如 http://127.0.0.1:7890（维基直连超时时必填）")
    a = ap.parse_args()

    fp = load_module()
    if a.proxy:
        fp.set_proxy(a.proxy)
        print(f"使用代理 {a.proxy}")

    if a.ids:
        missing = [int(x) for x in a.ids.split(",") if x.strip()]
    else:
        p = os.path.join(TMP, "miss_all.json")
        if not os.path.exists(p):
            print(f"[错误] 缺少 {p}（先跑 _fetch_posters.py --check）")
            return 1
        missing = [int(x) for x in json.load(open(p, encoding="utf-8"))]
    if a.limit:
        missing = missing[:a.limit]
    print(f"待补 {len(missing)} 部")

    # movieId → (year, title)
    rows = {}
    for ln in open(os.path.join(ROOT, "data", "movies.dat"), encoding="utf-8"):
        c = ln.rstrip("\n").split("\t")
        if len(c) >= 4:
            try:
                rows[int(c[0])] = (c[1], c[3])
            except ValueError:
                pass

    # imdbId → movieId
    im2mid = {}
    if os.path.exists(LINKS):
        for ln in open(LINKS, encoding="utf-8"):
            c = ln.rstrip("\n").split(",")
            if len(c) >= 2 and c[1].isdigit():
                im2mid["tt" + c[1].zfill(7)] = int(c[0])
    extra_p = os.path.join(TMP, "movies_extra.json")
    if os.path.exists(extra_p):
        for o in json.load(open(extra_p, encoding="utf-8")):
            im2mid[o["imdb"]] = o["movieId"]

    want_im = {im2mid[i]: i for i in missing if i in im2mid}
    print(f"其中 {len(want_im)} 部能对应到 IMDb id")

    # 扫 akas：收集每部片的「原语言片名 + 语言」
    akas = {}
    if os.path.exists(AKAS):
        # 注意：want_im 是 imdb → movieId；akas 的行首是 imdb id（tt…），
        # 所以过滤要用 want_im.keys()（tconst 集合），不是 values（movieId 集合）。
        wantset = set(want_im.keys())
        with open(AKAS, encoding="utf-8", errors="replace") as f:
            next(f)
            for ln in f:
                c = ln.rstrip("\n").split("\t")
                if len(c) < 8 or c[0] not in wantset:
                    continue
                tid, title, region, lang, types, isorig = c[0], c[2], c[3], c[4], c[5], c[7]
                rec = akas.setdefault(tid, {"orig": None, "alt": []})
                # 原语言名：优先看 types=original；lang 常为 \N，所以不强求语言码，
                # 由 guess_lang 从字形补（土耳其语片名的 ığşçöü、俄语西里尔等）
                if isorig == "1" or "original" in (types or ""):
                    rec["orig"] = (title, lang)
                # 收集所有「像本地语」的别名：语言码已知，或字形能猜出语种
                lc = lang.strip()
                if lc and lc != NONE:
                    if lc in LANGMAP:
                        rec["alt"].append((title, lc))
                else:
                    g = guess_lang(title)
                    if g:
                        rec["alt"].append((title, g))
    else:
        print(f"[warn] 缺少 {AKAS}，只能用英文片名猜语言")

    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    ok = miss = 0
    for mid in missing:
        y, t = rows.get(mid, ("", ""))
        yi = int(y) if y.isdigit() else 0
        tid = want_im.get(mid)
        rec = akas.get(tid, {"orig": None, "alt": []}) if tid else {"orig": None, "alt": []}

        # 候选顺序：原语言片名（对应语维基）→ 各语言 aka → 英文片名多语维基
        cands = []
        if rec["orig"]:
            _t0, _l0 = rec["orig"]
            cands.append((_t0, (_l0.strip() if _l0 else "") or (guess_lang(_t0) or "")))
        cands.extend(rec["alt"])
        src = None
        for title, lang in cands:
            lc = (lang or "").strip()
            if lc == NONE:
                lc = ""
            sub = LANGMAP.get(lc) or (LANGMAP.get(guess_lang(title) or "") if title else None)
            if not sub:
                continue
            try:
                j = fp.api({"action": "query", "titles": title, "prop": "pageimages",
                            "format": "json", "pithumbsize": "400",
                            "pilicense": "any", "redirects": "1"}, lang=sub)
            except Exception:
                continue
            for p in (j.get("query", {}).get("pages") or {}).values():
                if "missing" in p:
                    continue
                s = fp._thumb_of(p)
                if s:
                    src = s
                    break
            if src:
                break
        if not src:
            try:
                src = fp.wiki_other_lang(t, yi)
            except Exception:
                src = None
        if not src:
            miss += 1
            print(f"  MISS {mid} {t[:46]}")
            continue
        try:
            r = fp.grab(src)
            if not r:
                miss += 1
                continue
            with open(os.path.join(GET, f"{mid}.jpg"), "wb") as f:
                f.write(fp.to_poster_jpeg(r[0]))
            cache[str(mid)] = src
            ok += 1
            print(f"  OK   {mid} {t[:40]:40} <- {src.rsplit('/',1)[-1][:46]}")
        except Exception as e:
            miss += 1
            print(f"  ERR  {mid} {type(e).__name__}")

    json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
    print(f"完成：新增 {ok}，仍缺 {miss}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
