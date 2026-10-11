#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_posters.py —— 海报缓存「正确性」复查（不只是覆盖，而是有没有抓错片）。

为什么需要这个脚本
------------------------------------------------------------------
海报抓取的最后几级回退（opensearch 检索 / REST summary / iTunes 模糊匹配）
在冷门片上会返回**同名或近似名的无关图片**。实测抓到的错图例子：

    Yoksul (1986)              ← Japanese_Ohka_rocket_plane.jpg（二战火箭机照片）
    The Miracle 2: Love (2019) ← Eurythmics_MOL.jpg（乐队专辑封面）
    Yesterday's Past (2021)    ← Joe_Satriani_-_Shapeshifting.jpg（吉他手海报）
    The Foster Brothers (1976) ← Freakyfriday1976.jpg（《怪诞星期五》海报）

**错图比缺图更糟**：缺图时前端会落到首字母色块，用户知道「这张没有」；
错图则会让人以为数据错了、不可信。所以拉完之后必须复查一轮正确性。

判定方式
------------------------------------------------------------------
把缓存里记录的图片 URL 与影片标题做「文件名 ↔ 片名」相似度比较：

  * 文件名里没有有效词（poster.jpg / 12345.jpg）→ **无法判断，保留**
  * 相似度达标（含音译/拼写变体）→ 保留
  * 相似度不达标 → 判为可疑，默认移出缓存（`--delete` 时并删掉图片文件），
    让流程用带校验的回退重新抓；抓不到就老实显示占位块

用法：
    python app/pre/audit_posters.py                # 只报告
    python app/pre/audit_posters.py --delete       # 删掉可疑项（图片移到 _build_tmp/rejected/）
    python app/pre/audit_posters.py --threshold 0.55
"""
import argparse
import difflib
import json
import os
import re
import shutil
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # app/
PROJ = os.path.dirname(ROOT)
POSTERS = os.path.join(ROOT, "frontend", "assets", "posters")
CACHE = os.path.join(PROJ, "_build_tmp", "poster_resolve_cache.json")
QUARANTINE = os.path.join(PROJ, "_build_tmp", "rejected_posters")

STOP = {"a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "at",
        "part", "vol", "film", "movie", "aka", "poster", "ver", "ver2"}


def norm(s):
    s = urllib.parse.unquote(s or "")
    s = re.sub(r"\.(jpg|jpeg|png|webp|svg)$", "", s, flags=re.I)
    s = re.sub(r"^\d+px-", "", s)                # 维基缩略图前缀 500px-
    s = re.sub(r"[_\-]+", " ", s)
    s = s.lower()
    s = re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", s)
    return " ".join(w for w in s.split() if w not in STOP)


def similar(a, b):
    """0~1 相似度：先看整串，再看 token 覆盖率（容忍音译/词序差异）。"""
    if not a or not b:
        return 0.0
    whole = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    cov = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(whole, cov)


def load_titles():
    """movieId → 原始标题（含年份括号）。"""
    out = {}
    p = os.path.join(ROOT, "data", "movies.dat")
    with open(p, encoding="utf-8") as f:
        for ln in f:
            if not ln or ln[0] == "#":
                continue
            c = ln.rstrip("\n").split("\t")
            if len(c) >= 4:
                try:
                    out[int(c[0])] = c[3]
                except ValueError:
                    pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true",
                    help="把可疑海报移出 assets（移到 _build_tmp/rejected_posters/）")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="相似度阈值（默认 0.5，越低越宽松）")
    ap.add_argument("--show", type=int, default=30, help="打印前 N 条可疑")
    a = ap.parse_args()

    if not os.path.exists(CACHE):
        print(f"[错误] 缺少解析缓存 {CACHE}（先跑 _fetch_posters.py）")
        return 1
    cache = json.load(open(CACHE, encoding="utf-8"))
    titles = load_titles()
    print(f"缓存 {len(cache)} 条，影片表 {len(titles)} 部，阈值 {a.threshold}")

    suspect, ok, unknown = [], 0, 0
    for mid, url in cache.items():
        t = titles.get(int(mid))
        if not t:
            continue
        fn = url.rsplit("/", 1)[-1].split("?")[0]
        nf, nt = norm(fn), norm(t)
        if not nf:
            unknown += 1                       # 文件名没有有效词，无从判断
            continue
        if similar(nf, nt) >= a.threshold:
            ok += 1
        else:
            suspect.append((int(mid), t, nf, similar(nf, nt)))

    print(f"  通过 {ok} · 可疑 {len(suspect)} · 文件名无信息（保留）{unknown}")
    suspect.sort(key=lambda x: x[3])
    for mid, t, nf, s in suspect[:a.show]:
        print(f"    id={mid:<7} sim={s:.2f}  {t[:44]:44} <- {nf[:44]}")

    if a.delete and suspect:
        os.makedirs(QUARANTINE, exist_ok=True)
        moved = 0
        for mid, _t, _nf, _s in suspect:
            src = os.path.join(POSTERS, f"{mid}.jpg")
            if os.path.exists(src):
                shutil.move(src, os.path.join(QUARANTINE, f"{mid}.jpg"))
                moved += 1
            cache.pop(str(mid), None)
        json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
        print(f"  已隔离 {moved} 张可疑海报 → {QUARANTINE}")
        print(f"  缓存已更新（{len(cache)} 条），重跑 _fetch_posters.py 可带校验重抓")
    elif suspect:
        print("  （未加 --delete：仅报告，未改动文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
