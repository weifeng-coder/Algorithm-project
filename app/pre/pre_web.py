#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_web.py —— 场景 D：Stanford Web 图 → 搜索排序场景数据

数据来源：https://snap.stanford.edu/data/web-Google.txt.gz（875,713 节点 /
5,105,039 边，文本边表 FromNodeId\\tToNodeId，# 开头为注释行）。

排序职责：
  1) CSR 建图：510 万条边按 key = srcDense<<20 | dstDense 编码成 int64 键，
     排序后分段 —— 图加载的真实瓶颈就是一次大排序（postings.dat，40 位键）；
  2) 节点 id 压缩：原始 id 稀疏（最大 ~9.9×10⁶），用 dict 压成稠密 id
     (<2²⁰) 后才能位打包 —— 稠密化本身即一次"键域变换"教学点；
  3) entities.val = 入度（PageRank 之前的"重要性"代理），检索/榜单用；
  4) PageRank 分值键由 C++ 侧 webgraph.h 计算后追加 score.dat（浮点→int64
     保序映射，见该文件）。

运行：python app/pre/pre_web.py <web-Google.txt.gz 路径>
"""

import gzip
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (JsonlGzWriter, outdir_of, write_entities, write_meta,
                    write_keys, write_sample, write_stats, write_tiers)

SRC_URL = "https://snap.stanford.edu/data/web-Google.txt.gz"


def main(srcgz):
    outdir = outdir_of("web")
    os.makedirs(outdir, exist_ok=True)
    dense = {}          # 原始id → 稠密id
    names = []          # 稠密id → "Node #原始id"
    indeg = []          # 稠密id → 入度
    outdeg = []
    keys = []           # array of int64: srcDense<<20 | dstDense

    print(f"[web] 流式解析 {srcgz} ...")
    with gzip.open(srcgz, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            a, b = line.split()
            sa = dense.get(a)
            if sa is None:
                sa = dense[a] = len(names)
                names.append(f"Node #{a}")
                indeg.append(0)
                outdeg.append(0)
            sb = dense.get(b)
            if sb is None:
                sb = dense[b] = len(names)
                names.append(f"Node #{b}")
                indeg.append(0)
                outdeg.append(0)
            keys.append((sa << 20) | sb)
            outdeg[sa] += 1
            indeg[sb] += 1
    n_nodes, n_edges = len(names), len(keys)
    print(f"[web] 节点 {n_nodes}，边 {n_edges}")

    # ---- 排序键（建 CSR 的负载）----
    tiers = write_tiers(outdir, keys)
    del keys

    # ---- entities.dat：val = 入度（Web 图中"被指向"的受欢迎程度）----
    ents = [{"id": i, "year": 0, "category": "(web)", "val": indeg[i], "name": names[i]}
            for i in range(n_nodes)]
    write_entities(os.path.join(outdir, "entities.dat"), ents)

    # ---- records.jsonl.gz：全量边表（JSON 交付形态）----
    print("[web] 写 records.jsonl.gz ...")
    max_in = max(indeg); argmax_in = indeg.index(max_in)
    jw = JsonlGzWriter(os.path.join(outdir, "records.jsonl.gz"))

    # records 需要原始 id 对 —— 重新流式读一遍（省内存：不再保存映射）
    # 但写 JSON 希望用原始 id —— dense 反查只在 sample 层面做代价高；
    # 折中：第二遍流式读取，输出稠密对（报告注明 id 为稠密化 id）。
    with gzip.open(srcgz, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            a, b = line.split()
            jw.write({"s": dense[a], "d": dense[b]})
    jw.close()

    # ---- sample（重新解析压缩包头部即可得前若干条）----
    sample = []
    with gzip.open(srcgz, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            a, b = line.split()
            sample.append({"s": dense[a], "d": dense[b],
                           "s_orig": a, "d_orig": b})
            if len(sample) >= 200:
                break
    write_sample(os.path.join(outdir, "records_sample.json"), sample)

    # ---- meta ----
    meta = {
        "scn": "web",
        "name": "网页图排序（PageRank）",
        "domain": "搜索引擎 —— 题目原文：搜索结果按重要性从高到低显示",
        "source": SRC_URL,
        "downloaded": "2026-09-28",
        "keyPack": "key = srcDense<<20 | dstDense（40 位）",
        "attrBits": 0, "subBits": 20,
        "entityLabel": "网页节点", "subLabel": "出边", "attrLabel": "",
        "countLabel": "出边数", "valLabel": "入度",
        "rankModes": {"pop": "出度关注度（出边数↓）", "val": "入度（被指向数↓）"},
        "extra": ["pagerank", "score.dat"],
    }
    write_meta(outdir, meta)

    # ---- stats ----
    write_stats(outdir, [
        f"dataset = Stanford web-Google（{SRC_URL}）",
        f"nodes = {n_nodes}  edges = {n_edges}",
        f"node id 压缩: 稀疏原始id → 稠密id (<2^20)，字典条目 {n_nodes}",
        f"max out-degree = {max(outdeg)} (node {outdeg.index(max(outdeg))})",
        f"max in-degree  = {max_in} (node {names[argmax_in]})",
        f"postings.dat = {tiers['full'][0]} keys / {tiers['full'][1]/1048576:.1f} MB",
        "键打包: srcDense<<20 | dstDense —— 排序后线性分段即 CSR 行索引",
    ])
    print(f"[web] 完成: postings={tiers['full'][0]} keys, entities={n_nodes}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "../downloads/web/web-Google.txt.gz"
    main(src)
