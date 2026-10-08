#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Amazon 电商场景：用 meta_Electronics.json.gz 的真实标题替换 entities.dat 里的 ASIN 代号。

- entities.dat 第 5 列 name:  ASIN  →  "标题 (ASIN)"（无标题的保留 ASIN）
- amazonboard.json: scatter / hist 里的 ASIN 同步替换为可读名称
- 覆盖率如实打印；标题清洗（制表符/换行/HTML 实体），避免破坏 TSV 结构
- 原文件备份到 downloads/amazon_entities_asin.bak
"""
import ast
import gzip
import html
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "amazon")
DL = os.path.join(ROOT, "..", "..", "downloads", "meta_Electronics.json.gz")

WS = re.compile(r"\s+")
TAG = re.compile(r"<[^>]{1,60}>")


def clean(t, maxlen=84):
    if not isinstance(t, str):
        return ""
    t = TAG.sub(" ", t)
    t = html.unescape(t)
    t = WS.sub(" ", t).strip()
    if len(t) > maxlen:
        t = t[: maxlen - 1].rstrip() + "…"
    return t


def main():
    if not os.path.exists(DL):
        sys.exit("meta_Electronics.json.gz 不存在，先下载：snap.stanford.edu meta_Electronics")
    titles = {}
    bad = 0
    with gzip.open(DL, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = ast.literal_eval(line)
            except (ValueError, SyntaxError, MemoryError):
                bad += 1
                continue
            asin = d.get("asin")
            t = clean(d.get("title"))
            if asin and t:
                titles[asin] = t
    print(f"meta 解析完成：titles={len(titles)} bad_lines={bad}")

    ent = os.path.join(DATA, "entities.dat")
    bak = os.path.join(DATA, "..", "..", "..", "..", "downloads", "amazon_entities_asin.bak")
    if not os.path.exists(bak):
        shutil.copyfile(ent, bak)
        print("backup →", os.path.normpath(bak))

    n = hit = 0
    tmp = ent + ".tmp"
    with open(ent, encoding="utf-8") as f, open(tmp, "w", encoding="utf-8") as out:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5:
                out.write(line)
                continue
            n += 1
            asin = p[4]
            t = titles.get(asin)
            if t:
                p[4] = f"{t} ({asin})"
                hit += 1
            out.write("\t".join(p) + "\n")
    os.replace(tmp, ent)
    print(f"entities.dat: {hit}/{n} 命中标题 ({hit / n * 100:.1f}%)")

    bp = os.path.join(DATA, "amazonboard.json")
    if os.path.exists(bp):
        b = json.load(open(bp, encoding="utf-8"))
        k = 0
        for row in b.get("scatter", []):
            if len(row) >= 4 and isinstance(row[2], str):
                asin = row[2]
                t = titles.get(asin)
                if t:
                    row[2] = f"{t} ({asin})"
                    k += 1
        for v in (b.get("hist") or {}).values():
            t = titles.get(v.get("asin", ""))
            if t:
                v["name"] = t
        json.dump(b, open(bp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print(f"amazonboard.json scatter 改名 {k} 条")

    cov = {"meta_titles": len(titles), "entities": n, "entities_with_title": hit,
           "bad_meta_lines": bad,
           "note": "标题来自 Amazon Electronics 商品元数据（snap.stanford.edu），抓取后本地化；缺标题的商品保留 ASIN"}
    json.dump(cov, open(os.path.join(DATA, "namecoverage.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("namecoverage.json 完成")


if __name__ == "__main__":
    main()
