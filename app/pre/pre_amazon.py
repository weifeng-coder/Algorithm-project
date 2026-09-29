#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_amazon.py —— 场景 B：Amazon Electronics 评论 → 商品排序场景数据

数据来源：SNAP Amazon productGraph
https://snap.stanford.edu/data/amazon/productGraph/categoryFiles/ratings_Electronics.csv
（782 万条评分，四列 userId,itemId,rating,timestamp —— 与 MovieLens ratings 同构）

键打包（多场景应用方案 B 节）：
    key = itemDense << 26 | userDense << 4 | ratingIdx
    （商品 63,176 < 2^16，用户 169 万 < 2^22，评分 5 档 4 位；共 42 位）
    ratingIdx = int(rating*2)-2 ∈ 0..8（0.5 星步长）→ mean = 0.5*avg + 0.5 星
排序任务：商品关注度榜单（评论数↓）、好评度榜单（均分↓）——多级键编码，
与 movie 场景同源；ASIN 稠密化 = 键域变换教学点。

运行：python app/pre/pre_amazon.py <ratings_Electronics.csv 路径>
"""

import os
import sys
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (JsonlGzWriter, outdir_of, human, write_entities, write_meta,
                    write_sample, write_stats, write_tiers)

SRC_URL = ("https://snap.stanford.edu/data/amazon/productGraph/"
           "categoryFiles/ratings_Electronics.csv")


def main(srccsv):
    outdir = outdir_of("amazon")
    os.makedirs(outdir, exist_ok=True)
    item2id, user2id = {}, {}
    itemNames = []          # 稠密 id → ASIN
    keys = array("q")
    n = 0
    print(f"[amazon] 流式解析 {srccsv} ...")
    with open(srccsv, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 3:
                continue
            uid, iid, rating = p[0], p[1], p[2]
            try:
                stars = float(rating)
            except ValueError:
                continue
            ridx = int(stars * 2) - 2          # 1.0→0 ... 5.0→8
            if ridx < 0 or ridx > 8:
                continue
            iidD = item2id.get(iid)
            if iidD is None:
                iidD = item2id[iid] = len(itemNames)
                itemNames.append(iid)
            uidD = user2id.get(uid)
            if uidD is None:
                uidD = user2id[uid] = len(user2id)
            keys.append((iidD << 28) | (uidD << 4) | ridx)
            n += 1
            if n % 1000000 == 0:
                print(f"  {human(n)} 行 ...")
    nItems, nUsers = len(itemNames), len(user2id)
    print(f"[amazon] 商品 {nItems}，用户 {nUsers}，评论 {n}")

    tiers = write_tiers(outdir, keys)
    del keys

    ents = [{"id": i, "year": 0, "category": "(product)", "val": 0, "name": itemNames[i]}
            for i in range(nItems)]
    write_entities(os.path.join(outdir, "entities.dat"), ents)

    print("[amazon] 写 records.jsonl.gz（第二遍流式）...")
    with JsonlGzWriter(os.path.join(outdir, "records.jsonl.gz")) as jw:
        cnt = 0
        with open(srccsv, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.rstrip("\n").split(",")
                if len(p) < 3:
                    continue
                try:
                    stars = float(p[2])
                except ValueError:
                    continue
                ridx = int(stars * 2) - 2
                if ridx < 0 or ridx > 8:
                    continue
                jw.write({"u": user2id[p[0]], "i": item2id[p[1]],
                          "r": ridx, "t": int(p[3]) if len(p) > 3 and p[3].isdigit() else 0})
                cnt += 1
                if cnt >= n:
                    break

    sample = []
    with open(srccsv, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 3:
                continue
            sample.append({"u": p[0], "i": p[1], "stars": p[2],
                           "t": int(p[3]) if len(p) > 3 and p[3].isdigit() else 0})
            if len(sample) >= 200:
                break
    write_sample(os.path.join(outdir, "records_sample.json"), sample)

    meta = {
        "scn": "amazon",
        "name": "电商商品排序",
        "domain": "供应链管理/电商 —— 题目原文：商品按照价格、好评度、购买量、关注度排序",
        "source": SRC_URL,
        "downloaded": "2026-09-28",
        "keyPack": "key = itemDense<<28 | userDense<<4 | ratingIdx（47 位；用户实际 4.8M，22 位不够，扩到 24 位）",
        "attrBits": 4, "subBits": 24,
        "entityLabel": "商品", "subLabel": "用户", "attrLabel": "评分档(0.5星步长)",
        "countLabel": "评论数", "valLabel": "—",
        "meanScale": 0.5, "meanOffset": 0.5,
        "rankModes": {"pop": "关注度（评论数↓）", "rat": "好评度（均分↓）"},
    }
    write_meta(outdir, meta)

    write_stats(outdir, [
        f"dataset = Amazon Electronics ratings（{SRC_URL}）",
        f"reviews = {n}  products = {nItems}  users = {nUsers}",
        f"id 压缩: ASIN/userId 字符串 → 稠密 id（字典 {nItems + nUsers} 条）",
        f"postings.dat = {tiers['full'][0]} keys / {human(tiers['full'][1])}B",
        "键打包: itemDense<<28 | userDense<<4 | ratingIdx（47 位 int64）",
        "评分档: 0..8（0.5 星步长）；mean 换算 meanScale=0.5 meanOffset=0.5",
    ])
    print(f"[amazon] 完成: {n} reviews, {nItems} items")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "../downloads/amazon/ratings_Electronics.csv"
    main(src)
