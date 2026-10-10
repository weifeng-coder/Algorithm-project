#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_fetch_posters.py —— 为「当前可达榜单」批量补齐海报缩略图。

用法：
    python app/pre/_fetch_posters.py                 # 覆盖默认可达范围（推荐）
    python app/pre/_fetch_posters.py --k 200         # 放宽每个榜单的 Top-K
    python app/pre/_fetch_posters.py --ids 318,296   # 只补指定 movieId
    python app/pre/_fetch_posters.py --check         # 只体检，不下载

为什么需要「按可达范围」拉取：
    海报缓存若只按某个固定 Top-N 拉取，一旦用户把 K 调大、切换排序依据
    （关注度 / 好评度 / 评分优先）或换类型，榜单会换成另一批影片，缓存没覆盖
    的部分就只能退化到首字母色块。本脚本把「所有 类型 × 排序依据 × 票数门槛
    的 Top-K 并集」都算出来批量补齐，并做多轮复查，直到连续一轮无新增缺口。

数据源（均为免密钥的公网接口，逐个降级）：
    1) Wikipedia pageimages（pilicense=any）—— 主源。先按清理后的片名取词条，
       再用年份消歧（"X (1994 film)" / "X (film)"），最后回退 opensearch 检索。
    2) Wikipedia REST summary —— 词条正文首图（pageimages 无图时）。
    3) iTunes Search API —— 兜底。

每张图都会做「可解码性」校验（PNG/JPEG 魔数 + 最小体积 + 最小边长），
不合格的图片直接丢弃并记入缺口清单，避免把 404 页面 / HTML 错误页当成 jpg 存下来。
下载后统一转成宽度 <= 300px 的 JPEG（体积从数百 KB 降到 ~20KB），
否则上千部影片的缓存会膨胀到几十 MB，既拖慢首屏也超出仓库体积惯例。
"""
import argparse
import io
import json
import os
import re
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:                     # 无 Pillow 时退回原图（体积偏大但功能可用）
    HAVE_PIL = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # app/
PROJ = os.path.dirname(ROOT)                                          # 工程根
OUT = os.path.join(ROOT, "frontend", "assets", "posters")
REPORT = os.path.join(PROJ, "_build_tmp", "poster_report.json")
CACHE = os.path.join(PROJ, "_build_tmp", "poster_resolve_cache.json")
BASE = "http://127.0.0.1:8080"
UA = {
    # Wikimedia API 政策要求可识别的 UA（含用途与联系方式），
    # 否则会被批量返回 429。这一条是「大批量拉取能跑通」的前提。
    "User-Agent": ("CineRank-course/1.0 (algorithm course project; "
                   "poster cache; contact: cinerank-course@example.invalid) "
                   "python-urllib/3"),
    "Accept": "application/json,image/*",
}
CTX = ssl.create_default_context()

# 片名 → 维基词条名（维基用惯用英文名，MovieLens 用原始片名，需人工补别名）
ALIASES = {
    "Seven (a.k.a. Se7en)": "Se7en",
    "Raiders of the Lost Ark (Indiana Jones and the Raiders of the Lost Ark)": "Raiders of the Lost Ark",
    "Star Wars: Episode IV - A New Hope": "Star Wars (film)",
    "Star Wars: Episode V - The Empire Strikes Back": "The Empire Strikes Back",
    "Star Wars: Episode VI - Return of the Jedi": "Return of the Jedi",
    "Terminator 2: Judgment Day": "Terminator 2: Judgment Day",
    "The Lord of the Rings: The Fellowship of the Ring": "The Lord of the Rings: The Fellowship of the Ring",
    "The Lord of the Rings: The Two Towers": "The Lord of the Rings: The Two Towers",
    "The Lord of the Rings: The Return of the King": "The Lord of the Rings: The Return of the King",
    # 非英语片：维基词条用英文通名，需按原名/惯用译名补别名
    "Spirited Away (Sen to Chihiro no kamikakushi)": "Spirited Away",
    "City of God (Cidade de Deus)": "City of God (2002 film)",
    "Princess Mononoke (Mononoke-hime)": "Princess Mononoke",
    "Seven Samurai (Shichinin no samurai)": "Seven Samurai",
    "My Neighbor Totoro (Tonari no Totoro)": "My Neighbor Totoro",
    "Lives of Others, The (Das leben der Anderen)": "The Lives of Others",
    "Come and See (Idi i smotri)": "Come and See",
    "Harakiri (Seppuku)": "Harakiri (1962 film)",
    "High and Low (Tengoku to jigoku)": "High and Low (1963 film)",
    "Whiplash": "Whiplash (2014 film)",
    "Band of Brothers": "Band of Brothers (miniseries)",
    "Twin Peaks": "Twin Peaks",
    "Black Mirror": "Black Mirror",
}


# ------------------------------------------------------------------ HTTP
_rl_lock = threading.Lock()
_rl_until = [0.0]          # 全局限流闸门：429 后所有线程一起退避


def _throttle():
    while True:
        with _rl_lock:
            wait = _rl_until[0] - time.time()
        if wait <= 0:
            return
        time.sleep(min(wait, 2.0))


# 代理支持：中国大陆网络环境下维基直连会超时（实测 en.wikipedia.org 30s 超时而
# iTunes/Grouplens 正常），此时必须走本地代理。通过 --proxy 或环境变量
# CINERANK_PROXY 指定；不设则直连。
PROXY = None
_opener = None


def set_proxy(url):
    """配置代理（如 http://127.0.0.1:7890）。传 None 恢复直连。"""
    global PROXY, _opener
    PROXY = url or None
    if PROXY:
        _opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    else:
        _opener = None


def _open(req, timeout):
    if _opener is not None:
        return _opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=CTX)


def fetch(url, timeout=25, retries=4):
    """带 429/5xx 退避重试的 GET。429 会触发全局闸门，避免并发线程继续加压。"""
    last = None
    for i in range(retries):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=UA)
            with _open(req, timeout) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):          # 词条不存在：无需重试
                raise
            if e.code == 429:
                with _rl_lock:
                    _rl_until[0] = max(_rl_until[0], time.time() + 3.0 * (i + 1))
            last = e
            time.sleep(1.0 * (i + 1))
        except Exception as e:                # 超时 / DNS / 连接重置：退避重试
            last = e
            time.sleep(0.8 * (i + 1))
    raise last


def api(params, timeout=20, lang="en"):
    """调用维基 API。lang 指定子域（en / ja / tr / hi …），用于多语言回退。"""
    host = "en.wikipedia.org" if lang == "en" else f"{lang}.wikipedia.org"
    u = f"https://{host}/w/api.php?" + urllib.parse.urlencode(params)
    raw, _ = fetch(u, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


# ------------------------------------------------------------------ 片名清洗
def clean_title(title):
    """片名规范化：去年份 → 去括号内原名 → 别名 → 逗号倒装。

    逗号倒装（"Mirror, The" → "The Mirror"）必须在**去掉括号内原名之后**做：
    MovieLens 把原名写在括号里（"Mirror, The (Zerkalo)"），若先做倒装，
    正则要求字符串以 The/A/An 结尾，而结尾是 "(Zerkalo)"，倒装就永远不会发生——
    这是之前一批《The Mirror》《The Postman》查不到图的原因。
    """
    t = re.sub(r"\s*\(\d{4}\)\s*$", "", title or "").strip()
    t = re.sub(r"\s*\(a\.k\.a\..*$", "", t).strip()
    t = ALIASES.get(t, t)
    if t in ALIASES.values():
        return t
    # 先去括号（含原名），再做逗号倒装
    plain = re.sub(r"\s*\([^)]*\)", "", t).strip()
    for cand in (plain, t):
        m = re.match(r"^(.*),\s*(The|A|An)$", cand, re.I)
        if m:
            return (m.group(2) + " " + m.group(1)).strip()
    return plain or t


def wiki_candidates(title, year):
    """按「越精确越靠前」生成词条候选名。

    MovieLens 的片名里常把**原名**放在括号里（《Beauty of the Day (Belle de jour)》、
    《Burnt by the Sun (Utomlyonnye solntsem)》），而维基本国语言词条用的是原名。
    所以除英文名外，还要把括号内的原名拆出来当候选——这一步对非英语片的
    命中率影响很大（不拆的话英文维基常查不到，多语言维基又不知道用哪个名字查）。
    """
    raw = re.sub(r"\s*\(\d{4}\)\s*$", "", title or "").strip()
    out = []

    # 1) 括号内的原名（形态像外文名时优先）：去掉尾部的 (original) 片段
    for inner in re.findall(r"\(([^)]+)\)", raw):
        cand = inner.strip()
        if not cand or re.fullmatch(r"\d{4}|film|miniseries|TV series|a\.k\.a\..*", cand, re.I):
            continue
        if re.fullmatch(r"(a\.k\.a\.)?\s*[\w\s:,'\-\.]+", cand) and len(cand) > 3:
            out.append(cand)
    # 2) 去掉所有括号的主名
    stripped = re.sub(r"\s*\([^)]*\)", "", raw).strip()
    base = clean_title(raw)
    for c in ([base, stripped] if stripped != base else [base]):
        if c:
            out.append(c)
    # 3) 年份/媒介消歧与逗号倒装
    for b in list(out):
        if year:
            out.append(f"{b} ({year} film)")
        out.append(f"{b} (film)")
        out.append(b + " (miniseries)")
        out.append(b + " (TV series)")
    seen, res = set(), []
    for c in out:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            res.append(c)
    return res


def _thumb_of(page):
    for key in ("thumbnail", "originalimage"):
        t = page.get(key)
        if t and t.get("source"):
            return t["source"]
    return None


# 判「这个维基词条是不是影视作品」：pageprops 给维基数据描述（"2024 film directed by X"），
# 是最干净的判据。没有它就会出事——查 "Conclave" 会命中「教皇选举」条目（配西斯廷礼拜堂照片）、
# 查 "Saw" 会命中「锯」条目、查 "Kneecap" 会命中「膝盖骨」条目。
_FILM_HINT = re.compile(r"\b(film|movie|miniseries|TV series|television series|"
                        r"animated series|anime|documentary|docudrama)\b", re.I)


def page_is_film(page):
    """词条描述里出现 film/movie/剧集 等词才算影视作品；无描述则放行（`--strict` 时可收紧）。"""
    props = page.get("pageprops") or {}
    desc = props.get("wikibase-shortdesc") or props.get("description") or ""
    if not desc:
        return True          # 没有描述信息时不武断否决，交给名字匹配把关
    return bool(_FILM_HINT.search(desc))


def _pages_with_props(j):
    return (j.get("query", {}).get("pages") or {}).values()


def wiki_batch(cands, size=50):
    """一次查询多个词条（MediaWiki 上限 50/请求）。返回 {请求名: 图片URL}。

    这是吞吐量关键：逐个词条查询要 1 次请求/片，批量后 1 次请求/50 片，
    上万部影片的补齐时间从数小时压到十几分钟。返回的映射同时按
    「规范化名」与「重定向目标名」登记，避免因为标题变体而漏掉已取到的图。
    """
    out = {}
    cands = [c for c in cands if c]
    nchunk = (len(cands) + size - 1) // size
    failed = 0
    for i in range(0, len(cands), size):
        chunk = cands[i:i + size]
        try:
            j = api({"action": "query", "titles": "|".join(chunk),
                     "prop": "pageimages|pageprops", "ppprop": "wikibase-shortdesc",
                     "format": "json", "pithumbsize": "400", "pilicense": "any",
                     "redirects": "1"})
        except Exception as e:
            failed += len(chunk)          # 请求失败 ≠ 没有图：交给后续复查轮次再试
            if failed <= size * 2:
                print(f"      [warn] 批次失败 {type(e).__name__}，继续…", flush=True)
            continue
        q = j.get("query") or {}
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
        # 词条名 → 图片（只收「确实是影视作品」的词条，见 page_is_film）
        by_title = {}
        for p in (q.get("pages") or {}).values():
            src = _thumb_of(p)
            if src and p.get("title") and page_is_film(p):
                by_title[p["title"]] = src
        for c in chunk:
            t = norm.get(c, c)
            t = redir.get(t, t)
            if t in by_title:
                out[c] = by_title[t]
        idx = i // size + 1
        if idx % 20 == 0 or idx == nchunk:
            print(f"      … 批次 {idx}/{nchunk} 命中 {len(out)}（失败 {failed}）",
                  flush=True)
    return out


# ------------------------------------------------------------------ 数据源
def wiki_pageimage(title, year):
    """主源：完整候选名逐个试 pageimages。"""
    for cand in wiki_candidates(title, year):
        try:
            j = api({"action": "query", "titles": cand, "prop": "pageimages|pageprops",
                     "ppprop": "wikibase-shortdesc", "format": "json",
                     "pithumbsize": "400", "pilicense": "any", "redirects": "1"})
        except Exception:
            continue
        for p in (j.get("query", {}).get("pages") or {}).values():
            src = _thumb_of(p)
            if src and page_is_film(p):
                return src
        time.sleep(0.12)
    return None


def wiki_search_batch(pairs, size=50, jobs=10):
    """批量检索回退：并发用 opensearch 拿候选词条，再一次性批量取图。

    opensearch 一次只能查一个词（MediaWiki 不支持多词合并），所以这一步用线程池
    并发；候选名汇总去重后走 pageimages 批量查询，请求数从 O(n) 降到 O(n/50)。
    """
    out = {}
    cand_for = {}          # mid -> [候选词条名]

    def look(mid, title, year):
        base = clean_title(title)
        for q in ([f"{base} {year} film", base] if year else [base]):
            try:
                j = api({"action": "opensearch", "search": q, "limit": "5",
                         "namespace": "0", "format": "json"})
            except Exception:
                continue
            names = j[1] if isinstance(j, list) and len(j) > 1 else []
            if names:
                return mid, names
        return mid, []

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(look, mid, t, y) for mid, (t, y) in pairs.items()]
        for fut in as_completed(futs):
            try:
                mid, names = fut.result()
            except Exception:
                continue
            if names:
                cand_for[mid] = names

    # 汇总候选 → 批量取图
    allc = []
    for names in cand_for.values():
        allc.extend(names)
    seen, uniq = set(), []
    for c in allc:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    imgmap = wiki_batch(uniq, size=size)

    # 候选按返回顺序定优先级（越靠前越可能是正解）；
    # **必须过 title_match**：opensearch 常给回同名无关作品，错图比缺图更糟。
    for mid, names in cand_for.items():
        title = pairs[mid][0]
        for c in names:
            if c in imgmap and title_match(title, c):
                out[mid] = imgmap[c]
                break
    return out


def wiki_search(title, year):
    """单个检索（兜底用；批量版覆盖不到时调用）。"""
    base = clean_title(title)
    for q in ([f"{base} {year} film", base] if year else [base]):
        try:
            j = api({"action": "query", "list": "search", "srsearch": q,
                     "srlimit": "5", "format": "json"})
        except Exception:
            continue
        hits = [h["title"] for h in j.get("query", {}).get("search", [])]
        if not hits:
            continue
        try:
            j2 = api({"action": "query", "titles": "|".join(hits),
                      "prop": "pageimages|pageprops", "ppprop": "wikibase-shortdesc",
                      "format": "json", "pithumbsize": "400", "pilicense": "any"})
        except Exception:
            continue
        for p in (j2.get("query", {}).get("pages") or {}).values():
            src = _thumb_of(p)
            if src and page_is_film(p) and title_match(title, p.get("title", "")):
                return src
        time.sleep(0.12)
    return None


def _norm_tokens(t):
    """片名 → 关键词集合（去括号年份、去括号原名、去标点、去停用词、小写）。"""
    t = re.sub(r"\s*\(\d{4}\)\s*$", "", t or "")
    # 去括号里的原文名/修饰：（Fa yeung nin wa）、(a.k.a. …)、(film)、(1994 film)
    t = re.sub(r"\s*\([^)]*\)", " ", t)
    t = t.lower()
    t = re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", t)
    stop = {"a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "at",
            "part", "vol", "film", "movie", "aka"}
    return {w for w in t.split() if w and w not in stop}


def title_match(query_title, candidate_title, min_ratio=0.6):
    """候选词条名与目标片名是否足够像（防止检索回退抓错片）。

    为什么必须校验：opensearch 对冷门片经常返回「同名的其它作品」——
    实测《The Foster Brothers (1976)》被检索到《Freaky Friday》的海报、
    《Solo Leveling: ReAwakening》被检索到一位演员的照片。**错图比缺图更糟**，
    所以这里要求候选名与片名的关键词重合度过关，否则宁可不要这张图。
    """
    q = _norm_tokens(query_title)
    c = _norm_tokens(candidate_title)
    if not q or not c:
        return False
    if q == c:
        return True
    inter = len(q & c)
    # 双向覆盖：候选里的关键词要覆盖目标的大部分（防止候选是无关长词条）
    cov_q = inter / len(q)
    cov_c = inter / len(c) if c else 0
    return cov_q >= min_ratio and cov_c >= 0.5


def wiki_other_lang(title, year, langs=None):
    """多语言维基回退：冷门非英语片在英文维基往往没有条目，但在本国语维基有。

    实测：《Atatürk II 1881–1919》英文无条目、土耳其语有；《Kesari Chapter 2》
    英文无、印地语有；《Demon Slayer 无限城篇》日文条目直接命中。所以英文源全空时，
    再按「原语言 → 维基子域」查一轮，命中率明显提升。

    默认语言表覆盖产量大、维基条目较全的语种；可用 langs 覆盖（主流程会先用
    IMDb akas 的原语言缩小范围，避免 18 种语言逐一无谓试探）。
    """
    base = clean_title(title)
    if langs is None:
        langs = ("ja", "tr", "hi", "te", "ta", "ml", "id", "ko", "fa", "zh", "de",
                 "fr", "es", "it", "ru", "pt", "pl", "cs")
    for lang in langs:
        try:
            j = api({"action": "query", "titles": base, "prop": "pageimages|pageprops",
                     "ppprop": "wikibase-shortdesc", "format": "json",
                     "pithumbsize": "400", "pilicense": "any", "redirects": "1"},
                    lang=lang, timeout=12)
        except Exception:
            continue
        q = j.get("query") or {}
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
        for p in (q.get("pages") or {}).values():
            if "missing" in p or not p.get("title"):
                continue
            # 本国语维基的 shortdesc 常常为空，所以这里放宽：只要名字对得上就收
            t = redir.get(norm.get(base, base), norm.get(base, base))
            if p["title"] != t and not title_match(title, p["title"]):
                continue
            src = _thumb_of(p)
            if src:
                return src
    return None


def wiki_rest(title, year):
    """再回退：REST summary 的 originalimage（不保证是海报，但比没有强）。"""
    for cand in wiki_candidates(title, year):
        if not title_match(title, cand):
            continue
        slug = urllib.parse.quote(cand.replace(" ", "_"))
        try:
            raw, _ = fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + slug,
                           timeout=20, retries=2)
        except Exception:
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        img = (data.get("originalimage") or data.get("thumbnail") or {}).get("source")
        if img:
            return img
    return None


def itunes_art(title, year):
    q = urllib.parse.urlencode({"term": title, "media": "movie", "entity": "movie",
                                "limit": "8", "country": "US"})
    try:
        raw, _ = fetch("https://itunes.apple.com/search?" + q, timeout=20)
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    want = re.sub(r"[^a-z0-9]+", "", title.lower())
    best = None
    for r in data.get("results") or []:
        art = r.get("artworkUrl100") or r.get("artworkUrl60")
        if not art:
            continue
        art = art.replace("100x100bb", "600x600bb").replace("60x60bb", "600x600bb")
        name = r.get("trackName") or r.get("collectionName") or ""
        key = re.sub(r"[^a-z0-9]+", "", name.lower())
        if want and (want in key or key in want):
            return art
        if best is None:
            best = art
    # 没有精确命中时**不返回 best**：iTunes 的模糊首条经常是无关影片，
    # 错图比缺图更糟（宁可让前端落回首字母色块）。
    return None


def rest_summary(candidates):
    """一次批量查 REST summary（比逐个词条快得多）。

    REST summary 一次只能查一个词条，但候选词条可以先做 pageimages 批量预筛：
    只有确实存在（非 missing）的候选才值得再查 REST。这里直接用批量 pageimages
    的 missing 标记做过滤，避免对着不存在的词条反复空转 5 次。
    """
    out = {}
    if not candidates:
        return out
    try:
        j = api({"action": "query", "titles": "|".join(candidates),
                 "prop": "pageimages|pageprops", "ppprop": "wikibase-shortdesc",
                 "format": "json", "redirects": "1"})
    except Exception:
        return out
    q = j.get("query") or {}
    norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
    redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
    existing = {p["title"] for p in (q.get("pages") or {}).values()
                if p.get("title") and "missing" not in p and page_is_film(p)}
    for c in candidates:
        t = norm.get(c, c)
        t = redir.get(t, t)
        if t in existing:
            out[c] = t
    return out


# ------------------------------------------------------------------ 图片校验
def img_dims(b):
    """返回 (w,h,fmt) 或 None（不可解码）。"""
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        try:
            w, h = struct.unpack(">II", b[16:24])
            return w, h, "png"
        except Exception:
            return None
    if b[:2] == b"\xff\xd8":
        i = 2
        while i < len(b) - 9:
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3):
                h, w = struct.unpack(">HH", b[i + 5:i + 9])
                return w, h, "jpg"
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            if i + 4 > len(b):
                break
            ln = struct.unpack(">H", b[i + 2:i + 4])[0]
            if ln < 2:
                break
            i += 2 + ln
        return None
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return 1, 1, "webp"
    return None


def grab(url):
    """下载并校验；合格返回 (bytes, fmt)，否则 None。"""
    raw, ctype = fetch(url, timeout=30)
    if not raw or len(raw) < 4000:              # 海报不会是几百字节
        return None
    d = img_dims(raw)
    if not d:
        return None
    w, h, fmt = d
    if w < 60 or h < 60:                        # 图标级尺寸，不是海报
        return None
    if h > 0 and w > 0 and (w / h) > 2.2:       # 极扁的横幅 / logo，不是海报
        return None
    return raw, fmt


def to_poster_jpeg(raw, target_w=300, quality=82):
    """统一转 JPEG 并限制宽度。

    落盘文件名固定是 `<id>.jpg`，而图源会返回 PNG/WebP/GIF，所以必须真正转码：
    早期版本在转码失败时回退写原图，结果 assets 里出现「扩展名 .jpg、内容却是 PNG」
    的文件（服务器按 .jpg 发 image/jpeg，浏览器靠嗅探仍能显示，但语义不符，
    且 Pillow 打开这类大 PNG 文本块会直接报错）。现在：转码失败就返回 None，
    调用方按「取不到图」处理，绝不写出名实不符的文件。
    """
    if not HAVE_PIL:
        return raw if raw[:2] == b"\xff\xd8" else None
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if im.width > target_w:
            nh = max(1, round(im.height * target_w / im.width))
            im = im.resize((target_w, nh), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=quality, optimize=True)
        jb = out.getvalue()
        return jb if len(jb) > 2000 else None
    except Exception:
        # PNG 文本块过大等解码错误：放宽限制再试一次
        try:
            from PIL import PngImagePlugin
            PngImagePlugin.MAX_TEXT_CHUNK = 100 * 1024 * 1024
            im = Image.open(io.BytesIO(raw))
            im.load()
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            if im.width > target_w:
                nh = max(1, round(im.height * target_w / im.width))
                im = im.resize((target_w, nh), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=quality, optimize=True)
            jb = out.getvalue()
            return jb if len(jb) > 2000 else None
        except Exception:
            return None


# ------------------------------------------------------------------ 缺口计算
def topk(params):
    u = BASE + "/api/movies/topk?" + urllib.parse.urlencode(params)
    raw, _ = fetch(u, timeout=60)
    return json.loads(raw.decode("utf-8")).get("items") or []


def reachable(k, minvotes_list, genres):
    """所有 类型 × 排序依据 × 票数门槛 的 Top-K 并集：id -> {title,year}。"""
    out = {}
    for g in genres:
        for mode in ("pop", "rat", "bayes"):
            for mv in minvotes_list:
                try:
                    items = topk({"k": k, "genre": g, "minvotes": mv, "mode": mode})
                except Exception as e:
                    print(f"  [warn] topk 失败 genre={g} mode={mode} mv={mv}: {e}")
                    continue
                for m in items:
                    out[m["id"]] = {"title": m["title"], "year": m.get("year", 0)}
    return out


def load_genres():
    try:
        raw, _ = fetch(BASE + "/api/genres", timeout=20)
        gs = json.loads(raw.decode("utf-8")).get("genres") or []
        return ["(all)"] + gs
    except Exception:
        return ["(all)"]


def resolve_titles(mids):
    """按 movieId 从 app/data/movies.dat 直接取片名（比走接口快，离线也能用）。

    movies.dat 行格式：id \\t year \\t primaryGenre \\t title
    返回 {mid: {"title":..., "year":...}}，按传入顺序保序。
    """
    path = os.path.join(ROOT, "data", "movies.dat")
    want = set(mids)
    got = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if not ln or ln[0] == "#":
                    continue
                c = ln.rstrip("\n").split("\t")
                if len(c) < 4:
                    continue
                try:
                    mid = int(c[0])
                except ValueError:
                    continue
                if mid in want:
                    got[mid] = {"title": c[3], "year": int(c[1]) if c[1].isdigit() else 0}
    out = {}
    for mid in mids:                       # 保序：优先级列表的顺序就是拉取顺序
        if mid in got:
            out[mid] = got[mid]
    miss = len(mids) - len(out)
    if miss:
        print(f"      [warn] {miss} 个 id 不在 movies.dat（movies_extra 未应用？）")
    return out


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=500,
                    help="每个榜单取 Top-K（默认 500，覆盖 UI 放大 K 的用法）")
    ap.add_argument("--ids", default="", help="只补这些 movieId（逗号分隔）")
    ap.add_argument("--ids-file", default="",
                    help="从 JSON 数组文件读 movieId 列表（优先级顺序，用于定向补榜）")
    ap.add_argument("--check", action="store_true", help="只体检：不下载，只报告缺口")
    ap.add_argument("--rounds", type=int, default=4, help="最多复查轮数（每轮补完再查一遍）")
    ap.add_argument("--jobs", type=int, default=8,
                    help="并发下载数（默认 8；维基对高并发会返回 429，别调太高）")
    ap.add_argument("--batch", type=int, default=50,
                    help="批量查询词条数（MediaWiki 上限 50）")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="每个任务结束后的额外停顿秒（默认 0，避免整体过慢）")
    ap.add_argument("--proxy", default=os.environ.get("CINERANK_PROXY", ""),
                    help="HTTP 代理，如 http://127.0.0.1:7890（维基直连超时时必填；"
                         "也可用环境变量 CINERANK_PROXY）")
    ap.add_argument("--lang-budget", type=int, default=600,
                    help="多语言维基阶段的时间预算秒（默认 600；超时即收工，"
                         "保证整轮一定收敛，不会卡在长尾上）")
    a = ap.parse_args()

    if a.proxy:
        set_proxy(a.proxy)
        print(f"[0/3] 使用代理 {a.proxy}")

    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)

    # 断点续跑：加载「movieId -> 已解析图片 URL」缓存
    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding="utf-8"))
            print(f"[0/3] 载入解析缓存 {len(cache)} 条（续跑用）")
        except Exception:
            cache = {}

    # 1) 目标集合
    if a.ids_file:
        want_ids = [int(x) for x in json.load(open(a.ids_file, encoding="utf-8"))]
        print(f"[1/3] 从 {a.ids_file} 读取 {len(want_ids)} 个待补 id，逐个解析片名…")
        target = resolve_titles(want_ids)
        print(f"      解析到片名 {len(target)} 部")
    elif a.ids:
        want_ids = {int(s) for s in a.ids.split(",") if s.strip()}
        print(f"[1/3] 计算可达榜单并集以解析 {len(want_ids)} 个 id 的片名…")
        full = reachable(a.k, [0, 50, 100, 1000], load_genres())
        target = {i: full[i] for i in want_ids if i in full}
        unknown = want_ids - set(target)
        if unknown:
            print(f"      [warn] {len(unknown)} 个 id 不在可达榜内，缺少片名，将跳过：{sorted(unknown)[:10]}")
    else:
        print(f"[1/3] 计算可达榜单并集（k={a.k}，逐个类型 × 3 种排序依据 × 4 档票数门槛）")
        target = reachable(a.k, [0, 50, 100, 1000], load_genres())
        print(f"      可达影片 {len(target)} 部")

    if a.check:
        miss = [i for i in target if not os.path.exists(os.path.join(OUT, f"{i}.jpg"))]
        print(f"[check] 缺口 {len(miss)}/{len(target)}")
        json.dump({"target": len(target), "missing": miss}, open(REPORT, "w"),
                  ensure_ascii=False, indent=1)
        return 0

    print(f"[2/3] 开始补齐海报（目标 {len(target)} 部，最多 {a.rounds} 轮复查，"
          f"下载并发 {a.jobs}）")
    counter = {"n": 0}
    lock = threading.Lock()
    stats = {"got": 0, "miss": 0}

    def download_one(job):
        """已解析到图片 URL 的：下载 → 校验 → 转码 → 落盘。

        转码失败（to_poster_jpeg 返回 None）时**绝不写字**：落盘文件名是 .jpg，
        写出非 JPEG 内容会造成名实不符（浏览器能嗅探显示，但语义已错）。
        """
        mid, src, used = job
        dest = os.path.join(OUT, f"{mid}.jpg")
        try:
            res = grab(src)
            if res:
                raw, _ = res
                jb = to_poster_jpeg(raw)
                if jb is None:
                    return False, used, "transcode failed"
                with open(dest, "wb") as f:
                    f.write(jb)
                return True, used, None
            return False, used, "grab rejected"
        except Exception as e:
            return False, used, f"{type(e).__name__}"

    for rnd in range(1, a.rounds + 1):
        todo = [(mid, info) for mid, info in target.items()
                if not (os.path.exists(os.path.join(OUT, f"{mid}.jpg"))
                        and os.path.getsize(os.path.join(OUT, f"{mid}.jpg")) > 4000)]
        if not todo:
            print(f"  第 {rnd} 轮：无缺口，收敛")
            break
        print(f"  第 {rnd} 轮：待补 {len(todo)} 部")
        got_this = 0

        # --- 阶段 0：命中「已解析 URL 缓存」的直接下载，省掉重复查询 ---
        resolved = {}            # mid -> (src, used)
        for mid, _info in todo:
            hit = cache.get(str(mid))
            if hit:
                resolved[mid] = (hit, "cache")
        if resolved:
            print(f"    [缓存] {len(resolved)} 部已有解析结果", flush=True)

        # --- 阶段 1：批量解析（50 词条/请求，先试「片名」「片名 (year film)」） ---
        for tag, cand_of in (("精确名", lambda t, y: [clean_title(t)]),
                             ("年份消歧", lambda t, y: wiki_candidates(t, y)[1:])):
            need = [(mid, info) for mid, info in todo if mid not in resolved]
            if not need:
                break
            # 建立 候选名 -> mid 的索引（同名词条只查一次）
            cand2mids = {}
            for mid, info in need:
                for c in cand_of(info["title"], info.get("year") or 0):
                    cand2mids.setdefault(c, []).append(mid)
            allc = list(cand2mids)
            print(f"    [{tag}] 候选词条 {len(allc)} 个…", flush=True)
            got = wiki_batch(allc, size=a.batch)
            for c, src in got.items():
                for mid in cand2mids.get(c, []):
                    resolved.setdefault(mid, (src, f"wiki/{tag}"))
            print(f"    [{tag}] 命中 {sum(1 for m in need if m[0] in resolved)}/{len(need)}",
                  flush=True)

        # --- 阶段 2：仍未命中的走检索回退（批量 opensearch）
        #     分批处理并在每批后立即下载：opensearch 是单条查询，受限流影响时
        #     可能跑很久；分批能让已解析到的图先落盘，进度可见、中断可续。 */
        chunk = max(50, a.batch * 4)
        total_new = 0
        for off in range(0, len(todo), chunk):
            rest = [(mid, info) for mid, info in todo[off:off + chunk]
                    if mid not in resolved]
            if not rest:
                continue
            pairs = {mid: (info["title"], info.get("year") or 0) for mid, info in rest}
            try:
                found = wiki_search_batch(pairs, size=a.batch, jobs=a.jobs)
            except Exception as e:
                print(f"      [warn] 批量检索失败：{type(e).__name__}", flush=True)
                found = {}
            for mid, src in found.items():
                resolved.setdefault(mid, (src, "wiki/search"))
            # 立即下载本批新解析到的（found 是 mid -> src）
            batch_jobs = [(mid, src, "wiki/search") for mid, src in found.items()
                          if not os.path.exists(os.path.join(OUT, f"{mid}.jpg"))]
            done = 0
            if batch_jobs:
                with ThreadPoolExecutor(max_workers=a.jobs) as ex:
                    for ok, _, _err in ex.map(
                            lambda j: download_one(j), batch_jobs):
                        if ok:
                            done += 1
            total_new += done
            got_this += done
            with lock:
                stats["got"] += done
            # 增量落盘解析缓存：限流中断也能续跑
            for mid in list(found):
                if os.path.exists(os.path.join(OUT, f"{mid}.jpg")):
                    cache[str(mid)] = found[mid]
            try:
                json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
            except Exception:
                pass
            print(f"    [检索回退] {min(off + chunk, len(todo))}/{len(todo)} "
                  f"命中 {len(found)}，本批落盘 {done}", flush=True)

        # --- 阶段 3：REST / iTunes 兜底 ---
        #     注意：wiki_rest 会逐个候选词条打 REST，一次 3~18 秒。对上千部冷门片
        #     逐个跑要几小时。所以先用批量 pageimages 把「根本不存在的词条」筛掉，
        #     只对真实存在的词条查 REST（这类才可能在正文里有图）。
        rest = [(mid, info) for mid, info in todo if mid not in resolved]
        if rest:
            print(f"    [REST/iTunes 兜底] {len(rest)} 部…", flush=True)
            # 3a) 收集候选并批量判存在性（顺带把命中的缩略图直接收下）
            cand2mids = {}
            for mid, info in rest:
                for c in wiki_candidates(info["title"], info.get("year") or 0):
                    cand2mids.setdefault(c, []).append(mid)
            exists = {}
            allc = list(cand2mids)
            for i in range(0, len(allc), a.batch):
                chunk = allc[i:i + a.batch]
                try:
                    j = api({"action": "query", "titles": "|".join(chunk),
                             "prop": "pageimages|pageprops",
                             "ppprop": "wikibase-shortdesc",
                             "format": "json", "redirects": "1",
                             "pithumbsize": "400", "pilicense": "any"})
                except Exception:
                    continue
                q = j.get("query") or {}
                norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
                redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
                by_title = {}
                for p in (q.get("pages") or {}).values():
                    # 必须确实是影视作品：否则 "Conclave"/"Saw"/"Kneecap" 会命中
                    # 教皇选举/锯子/膝盖骨等条目，抓回完全无关的图
                    if p.get("title") and "missing" not in p and page_is_film(p):
                        by_title[p["title"]] = _thumb_of(p)
                for c in chunk:
                    t = norm.get(c, c)
                    t = redir.get(t, t)
                    if t in by_title:
                        exists[c] = t
                        if by_title[t]:        # 这一轮就有缩略图
                            for mid in cand2mids.get(c, []):
                                resolved.setdefault(mid, (by_title[t], "wiki/pageimages"))

            # 3b) 只对存在但没缩略图的词条查 REST（标题用规范化后的真实词条名）
            todo_rest = []
            seen_t = {}
            for mid, info in rest:
                if mid in resolved:
                    continue
                for c in wiki_candidates(info["title"], info.get("year") or 0):
                    if c in exists:
                        todo_rest.append((mid, exists[c]))
                        break

            def one_rest(mid, art_title):
                slug = urllib.parse.quote(art_title.replace(" ", "_"))
                try:
                    raw, _ = fetch(
                        "https://en.wikipedia.org/api/rest_v1/page/summary/" + slug,
                        timeout=12, retries=1)
                    d = json.loads(raw.decode("utf-8"))
                except Exception:
                    return mid, None
                img = (d.get("originalimage") or d.get("thumbnail") or {}).get("source")
                return mid, img

            if todo_rest:
                print(f"      REST 直查 {len(todo_rest)} 部（已过滤不存在词条）",
                      flush=True)
                with ThreadPoolExecutor(max_workers=a.jobs) as ex:
                    for mid, src in ex.map(lambda p: one_rest(*p), todo_rest):
                        if src:
                            resolved.setdefault(mid, (src, "wiki/rest"))

            # 3c) 仍然没有的走 iTunes
            rest2 = [(mid, info) for mid, info in rest if mid not in resolved]
            if rest2:
                def one_it(mid, info):
                    try:
                        src = itunes_art(clean_title(info["title"]), info.get("year") or 0)
                    except Exception:
                        src = None
                    return mid, src
                with ThreadPoolExecutor(max_workers=a.jobs) as ex:
                    for mid, src in ex.map(lambda p: one_it(*p), rest2):
                        if src:
                            resolved.setdefault(mid, (src, "itunes"))

            # 3c2) 先把前三级已解析到的**立即下载落盘**。
            #   多语言维基是最慢的一级（18 个子域逐个试），早期版本把落盘放在它之后，
            #   导致它一卡住，前面几百个已经解析成功的海报也全都没写进 assets——
            #   表现就是「跑了几小时，海报数一个没涨」。先落盘再看慢阶段。
            ready = [(mid, src, used) for mid, (src, used) in resolved.items()
                     if not os.path.exists(os.path.join(OUT, f"{mid}.jpg"))]
            if ready:
                print(f"    先行落盘 {len(ready)} 张（多语言阶段之前）", flush=True)
                with ThreadPoolExecutor(max_workers=a.jobs) as ex:
                    for mid, ok, _err in ex.map(
                            lambda j: (j[0],) + download_one(j)[:2], ready):
                        if ok:
                            got_this += 1
                            stats["got"] += 1
                            cache[str(mid)] = resolved[mid][0]
                try:
                    json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
                except Exception:
                    pass
                print(f"    先行落盘完成，本轮累计新增 {got_this}", flush=True)

            # 3d) 多语言维基：冷门非英语片在英文维基常缺条目，本国语维基多有。
            #     并发跑，并在每批后立即落盘，避免长阶段看不到进度。
            rest3 = [(mid, info) for mid, info in rest if mid not in resolved]
            if rest3:
                print(f"      多语言维基 {len(rest3)} 部…"
                      f"（预算 {a.lang_budget}s，超时即收工）", flush=True)

                def one_lang(mid, info):
                    try:
                        src = wiki_other_lang(info["title"], info.get("year") or 0)
                    except Exception:
                        src = None
                    return mid, src

                done = 0
                t_lang = time.time()
                # 硬性时间预算：这一步要逐个试 18 个语言子域，最坏情况下极慢。
                # 前三级已落盘，这里拿到的都是长尾；用预算保证整体一定收敛。
                with ThreadPoolExecutor(max_workers=a.jobs) as ex:
                    futs = {ex.submit(one_lang, mid, info) for mid, info in rest3}
                    for fut in as_completed(futs):
                        if time.time() - t_lang > a.lang_budget:
                            for f in futs:
                                f.cancel()
                            print(f"      多语言维基超出预算，收工（已补 {done}）",
                                  flush=True)
                            break
                        try:
                            mid, src = fut.result()
                        except Exception:
                            continue
                        if src:
                            resolved.setdefault(mid, (src, "wiki/lang"))
                            # 立即下载，别等整阶段结束
                            try:
                                r = grab(src)
                                jb = to_poster_jpeg(r[0]) if r else None
                                if jb is not None:
                                    with open(os.path.join(OUT, f"{mid}.jpg"), "wb") as f:
                                        f.write(jb)
                                    cache[str(mid)] = src
                                    done += 1
                                    got_this += 1
                                    stats["got"] += 1
                            except Exception:
                                pass
                        if done and done % 25 == 0:
                            print(f"      … 多语言已补 {done}", flush=True)
                try:
                    json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
                except Exception:
                    pass
                print(f"      多语言维基新增 {done}", flush=True)

        # --- 阶段 4：并发下载落盘（跳过检索阶段已落盘的） ---
        jobs = [(mid, src, used) for mid, (src, used) in resolved.items()
                if not os.path.exists(os.path.join(OUT, f"{mid}.jpg"))]
        print(f"    下载 {len(jobs)} 张…", flush=True)
        with ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(download_one, j): j[0] for j in jobs}
            for fut in as_completed(futs):
                try:
                    ok, used, err = fut.result()
                except Exception as e:
                    ok, used, err = False, "", f"{type(e).__name__}"
                with lock:
                    counter["n"] += 1
                    if ok:
                        stats["got"] += 1
                        got_this += 1
                    else:
                        stats["miss"] += 1
                    n = counter["n"]
                    if n % 200 == 0 or n == len(jobs):
                        print(f"    … {n}/{len(jobs)} new={stats['got']} "
                              f"miss={stats['miss']}", flush=True)
                    elif err and err != "grab rejected" and stats["miss"] <= 30:
                        print(f"    [dbg] {err}", flush=True)
        print(f"  第 {rnd} 轮完成：新增 {got_this}／未取到 {len(todo) - got_this}", flush=True)
        # 解析成功的 URL 写回缓存：中途被限流/中断也能续跑，不必重查
        for mid, (src, used) in resolved.items():
            if os.path.exists(os.path.join(OUT, f"{mid}.jpg")):
                cache[str(mid)] = src
        try:
            json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
        except Exception:
            pass
        if got_this == 0:
            print(f"  第 {rnd} 轮无新增，剩余缺口视为无图可补")
            break

    # 3) 复查：写出最终缺口清单
    miss = sorted(i for i in target if not os.path.exists(os.path.join(OUT, f"{i}.jpg")))
    json.dump({"target": len(target), "missing": miss,
               "missing_titles": {str(i): target[i]["title"] for i in miss}},
              open(REPORT, "w"), ensure_ascii=False, indent=1)
    print(f"[3/3] 完成：新增 {stats['got']}，缺口 {len(miss)}/{len(target)}")
    print(f"      缺口清单 → {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
