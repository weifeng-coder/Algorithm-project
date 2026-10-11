#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— 多场景预处理公共库（数据契约的单一实现）

每个场景的预处理脚本 (pre_<scn>.py) 用本库把"爬取/下载的原始数据"转换成
统一的数据契约，输出到 app/data/<scn>/：

    postings.dat        uint64 小端键序列 —— 该场景的大排序负载（全量）
    postings_1e6.dat    等距抽样 1e6 键（规模阶梯，矩阵快速档）
    postings_1e5.dat    等距抽样 1e5 键
    ledger.dat          (可选) 台账序键 —— 块内有序、块间拼接的真实"块状近序"
    entities.dat        TSV: id \t year \t category \t val \t name（实体台账）
    meta.txt            key=value —— C++ EntityStore 读取的键解码参数
    meta.json           同内容的 JSON 形式 —— UI / 报告读取
    records.jsonl.gz    全量清洗记录，JSON Lines + gzip（"爬取成 JSON 格式"的
                        全量交付物；文本压缩率 ~8x，控制磁盘占用）
    records_sample.json 前 SAMPLE_N 条记录的直观 JSON 数组（免解压即可查看）
    stats.txt           数据体检摘要（人类可读）

磁盘口径（5GB 预算）：原始压缩包只作流式输入，逐场景处理完即删；
全量 JSON 以 .jsonl.gz 形态保留；最终每场景保留产物 ~50-150MB。
"""

import gzip
import json
import os
import struct

SAMPLE_N = 200          # records_sample.json 的记录数
TIER_1E6 = 1_000_000
TIER_1E5 = 100_000


def outdir_of(scn):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
    return os.path.join(root, "data", scn)


class KeyWriter:
    """uint64 小端键的缓冲写盘器"""

    def __init__(self, path, bufsz=1 << 20):
        self.f = open(path, "wb")
        self.buf = []
        self.n = 0
        self.bufsz = bufsz

    def push(self, key):
        self.buf.append(key)
        self.n += 1
        if len(self.buf) >= self.bufsz:
            self.flush()

    def flush(self):
        if self.buf:
            self.f.write(struct.pack(f"<{len(self.buf)}Q", *self.buf))
            self.buf = []

    def close(self):
        self.flush()
        self.f.close()


def write_keys(path, keys):
    """keys: array('q')/list —— 一次性写 .dat"""
    kw = KeyWriter(path)
    for k in keys:
        kw.push(k)
    kw.close()


def strided_sample(keys, n):
    """等距抽样 n 键（保序保分布，确定性）"""
    N = len(keys)
    if N <= n:
        return list(keys)
    step = N / n
    return [keys[int(i * step)] for i in range(n)]


def write_tiers(outdir, keys):
    """全量 + 1e6/1e5 抽样档；返回各档大小"""
    info = {}
    p = os.path.join(outdir, "postings.dat")
    write_keys(p, keys)
    info["full"] = (len(keys), os.path.getsize(p))
    for tag, n in (("1e6", TIER_1E6), ("1e5", TIER_1E5)):
        if len(keys) > n * 1.2:
            p = os.path.join(outdir, f"postings_{tag}.dat")
            write_keys(p, strided_sample(keys, n))
            info[tag] = (n, os.path.getsize(p))
    return info


class JsonlGzWriter:
    """JSON Lines + gzip 流式写盘器（全量记录的 JSON 交付形态）"""

    def __init__(self, path):
        self.f = gzip.open(path, "wt", encoding="utf-8", compresslevel=6)
        self.n = 0

    def write(self, obj):
        self.f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        self.f.write("\n")
        self.n += 1

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def write_sample(path, records, n=SAMPLE_N):
    """前 n 条记录 → 直观 JSON 数组"""
    sample = []
    for i, r in enumerate(records):
        if i >= n:
            break
        sample.append(r)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=1)


def write_entities(path, entities):
    """entities.dat TSV: id \t year \t category \t val \t name"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for e in entities:
            f.write(f"{e['id']}\t{e.get('year', 0)}\t{e.get('category', '(none)')}"
                    f"\t{e.get('val', 0)}\t{e['name']}\n")
    return len(entities)


def write_meta(outdir, meta):
    """meta.txt（C++ 读）+ meta.json（UI 读），内容同源"""
    with open(os.path.join(outdir, "meta.txt"), "w", encoding="utf-8", newline="\n") as f:
        for k, v in meta.items():
            if isinstance(v, str):
                f.write(f"{k}={v}\n")
            else:
                f.write(f"{k}={json.dumps(v, ensure_ascii=False)}\n")
    with open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


def write_stats(outdir, lines):
    with open(os.path.join(outdir, "stats.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def human(n):
    for unit in ("", "K", "M", "G"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}" if unit else str(n)
        n /= 1024
    return f"{n:.1f}T"
