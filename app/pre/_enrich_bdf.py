#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Precompute dashboard JSON for amazon / web / gene from local data only."""
import json
import math
import os
import struct
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def read_keys(path):
    raw = open(path, "rb").read()
    n = len(raw) // 8
    return struct.unpack(f"<{n}Q", raw[: n * 8])


def amazon():
    keys = read_keys(os.path.join(DATA, "amazon", "postings_1e6.dat"))
    star = Counter()
    per = defaultdict(lambda: [0] * 9)
    for k in keys:
        ridx = k & 15
        if ridx > 8:
            continue
        star[ridx] += 1
        per[k >> 28][ridx] += 1
    names = {}
    with open(os.path.join(DATA, "amazon", "entities.dat"), encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 5:
                names[int(p[0])] = p[4]
    rows = []
    for item, h in per.items():
        c = sum(h)
        if c < 20:
            continue
        mean = sum(i * v for i, v in enumerate(h)) / c * 0.5 + 1.0
        rows.append((item, c, mean, h))
    rows.sort(key=lambda r: -r[1])
    top = rows[:600]
    hist = {}
    for item, c, mean, h in rows[:2000]:
        hist[str(item)] = {"asin": names.get(item, str(item)), "n": c, "mean": round(mean, 3), "h": h}
    scatter = [[c, round(mean, 3), names.get(item, str(item)), item] for item, c, mean, _ in top]
    cnts = sorted((sum(h) for h in per.values()), reverse=True)
    buckets = [1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
    long_tail = []
    for i in range(len(buckets) - 1):
        lo, hi = buckets[i], buckets[i + 1]
        long_tail.append({"lo": lo, "hi": hi, "items": sum(1 for v in cnts if lo <= v < hi)})
    long_tail.append({"lo": buckets[-1], "hi": None, "items": sum(1 for v in cnts if v >= buckets[-1])})
    out = {
        "sample": len(keys),
        "items_in_sample": len(per),
        "star_hist": [star.get(i, 0) for i in range(9)],
        "star_labels": [f"{1 + i * 0.5:.1f}" for i in range(9)],
        "scatter": scatter,
        "hist": hist,
        "long_tail": long_tail,
        "note": "来自 postings_1e6.dat 等距抽样（100 万条），非全量 782 万条；星级 = ridx/2 + 1.0",
    }
    p = os.path.join(DATA, "amazon", "amazonboard.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("amazon", os.path.getsize(p), "items", len(per), "scatter", len(scatter))


def web():
    indeg = {}
    outdeg = {}
    with open(os.path.join(DATA, "web", "entities.dat"), encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 5:
                indeg[int(p[0])] = int(p[3])
    keys = read_keys(os.path.join(DATA, "web", "postings_1e6.dat"))
    for k in keys:
        s = k >> 20
        outdeg[s] = outdeg.get(s, 0) + 1
    # in-degree distribution, log-binned
    vals = list(indeg.values())
    cnt = Counter(vals)
    pts = [[k, v] for k, v in sorted(cnt.items()) if k > 0]
    logbins = []
    b = 1
    while b <= max(vals):
        hi = b * 2
        n = sum(v for k, v in cnt.items() if b <= k < hi)
        if n:
            logbins.append([b, n])
        b = hi
    # hub subgraph from pagerank top + sampled edges
    pr = json.load(open(os.path.join(DATA, "web", "pagerank.json"), encoding="utf-8"))
    top = pr.get("top", [])[:40]
    name2dense = {}
    with open(os.path.join(DATA, "web", "entities.dat"), encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 5:
                name2dense[p[4]] = int(p[0])
    hub_dense = {}
    for t in top:
        d = name2dense.get(t.get("name") or f"Node #{t['id']}")
        if d is not None:
            hub_dense[d] = t
    hubset = set(hub_dense)
    links = []
    seen = set()
    neigh = Counter()
    for k in keys:
        s, d = k >> 20, k & ((1 << 20) - 1)
        if s in hubset and d in hubset and s != d:
            e = (s, d)
            if e not in seen:
                seen.add(e)
                links.append({"source": s, "target": d})
        elif s in hubset:
            neigh[d] += 1
        elif d in hubset:
            neigh[s] += 1
    nodes = []
    for d, t in hub_dense.items():
        nodes.append({"id": d, "name": t.get("name"), "pr": t["pr"], "indeg": t["indeg"], "outdeg": t["outdeg"], "hub": True})
    extra = [n for n, _ in neigh.most_common(40)]
    for d in extra:
        if d in hubset:
            continue
        nodes.append({"id": d, "name": None, "pr": None, "indeg": indeg.get(d, 0), "outdeg": outdeg.get(d, 0), "hub": False})
    keep = {n["id"] for n in nodes}
    for k in keys:
        s, d = k >> 20, k & ((1 << 20) - 1)
        if s in keep and d in keep and s != d and (s in hubset or d in hubset):
            e = (s, d)
            if e not in seen:
                seen.add(e)
                links.append({"source": s, "target": d})
        if len(links) > 420:
            break
    # adjacency matrix: densest contiguous dense-id window (crawl order ≈ community blocks)
    W = 200
    M = (1 << 20) - 1
    win = Counter()
    for k in keys:
        s, d = k >> 20, k & M
        if s // W == d // W:
            win[s // W] += 1
    best_block, _ = win.most_common(1)[0]
    base = best_block * W
    cells = set()
    for k in keys:
        s, d = k >> 20, k & M
        if base <= s < base + W and base <= d < base + W:
            cells.add((s - base, d - base))
    order = list(range(base, base + W))
    scatter = []
    for t in pr.get("top", [])[:100]:
        scatter.append([t["indeg"], t["pr"], t.get("name"), t["outdeg"]])
    out = {
        "sample_edges": len(keys),
        "indeg_pts": pts[:400],
        "indeg_logbins": logbins,
        "hub": {"nodes": nodes, "links": links[:420]},
        "matrix": {"size": len(order), "base": base, "cells": sorted(cells),
                   "note": f"稠密 id {base}–{base + W - 1}（预处理按出现顺序编号，近似爬取社区）"},
        "pr_scatter": scatter,
        "note": "子图与矩阵来自 postings_1e6.dat 抽样边；入度来自 entities.dat 全量；数据集不提供节点名称",
    }
    p = os.path.join(DATA, "web", "webboard.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("web", os.path.getsize(p), "nodes", len(nodes), "links", len(links[:420]), "cells", len(cells))


def gene():
    seq = open(os.path.join(DATA, "gene", "seq.txt"), "rb").read().decode("ascii", "ignore")
    seq = "".join(c for c in seq if c in "ACGT")
    n = len(seq)
    bins = 600
    step = math.ceil(n / bins)
    gc = []
    skew = []
    cum = 0
    for i in range(0, n, step):
        w = seq[i : i + step]
        g = w.count("G")
        c = w.count("C")
        tot = len(w) or 1
        gc.append(round((g + c) / tot, 4))
        cum += g - c
        skew.append(cum)
    genes = json.load(open(os.path.join(DATA, "gene", "genegenes.json"), encoding="utf-8"))
    gl = genes.get("genes", [])
    dens = [0] * bins
    plus = minus = 0
    for g in gl:
        mid = (g["start"] + g["end"]) // 2
        b = min(bins - 1, mid // step)
        dens[b] += 1
        if g["strand"] == "+":
            plus += 1
        else:
            minus += 1
    lens = [g["end"] - g["start"] + 1 for g in gl]
    lens.sort()
    def q(p):
        return lens[int(p * (len(lens) - 1))] if lens else 0
    out = {
        "seq_len": n,
        "bin_bp": step,
        "bins": len(gc),
        "gc": gc,
        "gc_skew_cum": skew,
        "gene_density": dens,
        "genes_n": len(gl),
        "strand": {"+": plus, "-": minus},
        "gene_len_q": {"p10": q(0.1), "p50": q(0.5), "p90": q(0.9)},
        "first_genes": gl[:12],
        "note": "GC/偏斜按 ~7.7kb 窗口；基因来自 NCBI GFF（4,506 gene）",
    }
    p = os.path.join(DATA, "gene", "geneboard.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("gene", os.path.getsize(p), "bins", len(gc), "genes", len(gl))


if __name__ == "__main__":
    amazon()
    web()
    gene()
