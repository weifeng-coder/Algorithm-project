#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_gene.py —— 场景 F：E. coli K-12 基因组 → 后缀数组场景数据

数据来源：NCBI RefSeq GCF_000005845.2_ASM584v2（E. coli K-12 MG1655 基因组，
4.64 Mb）—— https://ftp.ncbi.nlm.nih.gov/genomes/refseq/bacteria/Escherichia_coli/
reference/GCF_000005845.2_ASM584v2/GCF_000005845.2_ASM584v2_genomic.fna.gz

本脚本只做"序列落盘 + JSON 画像"；排序负载（倍增轮复合键 / 后缀数组 /
BWT）由 C++ 侧 sufarray.h（sort_demo_app sufarr app/data/gene）计算生成：
  gene/postings.dat = 首轮复合键 (code[i]<<32|code[i+1])<<23|i —— 排序矩阵负载
  gene/sa.dat、gene/rounds.csv、gene/gene_stats.json —— 倍增过程与 BWT 统计

运行：python app/pre/pre_gene.py <ecoli.fna.gz 路径>
"""

import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import JsonlGzWriter, outdir_of, write_meta, write_sample, write_stats

SRC_URL = ("https://ftp.ncbi.nlm.nih.gov/genomes/refseq/bacteria/Escherichia_coli/"
           "reference/GCF_000005845.2_ASM584v2/GCF_000005845.2_ASM584v2_genomic.fna.gz")


def main(srcgz):
    outdir = outdir_of("gene")
    os.makedirs(outdir, exist_ok=True)
    comp = {"A": 0, "C": 0, "G": 0, "T": 0, "N": 0}
    header = ""
    seq = []
    print(f"[gene] 流式解析 {srcgz} ...")
    with gzip.open(srcgz, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith(">"):
                if not header:
                    header = line[1:].strip()
                continue
            for ch in line.strip().upper():
                if ch in comp:
                    comp[ch] += 1
                    seq.append(ch)
                else:
                    comp["N"] += 1
                    seq.append("N")
    n = len(seq)
    seq.append("$")           # 结尾哨兵（rank 0，小于任何碱基）
    text = "".join(seq)
    print(f"[gene] 长度 {n}，header: {header[:60]}")

    # seq.txt：倍增后缀数组的输入（C++ 侧读取）
    with open(os.path.join(outdir, "seq.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(text)

    # 全量 JSON（单记录：完整序列）—— "json格式" 交付形态，gzip 后 ~1.2MB
    with JsonlGzWriter(os.path.join(outdir, "records.jsonl.gz")) as jw:
        jw.write({"id": header, "source": SRC_URL, "length": n,
                  "alphabet": "A/C/G/T/N + $ 哨兵", "seq": text})

    write_sample(os.path.join(outdir, "records_sample.json"), [
        {"id": header, "length": n, "composition": comp,
         "first_120_bases": text[:120],
         "note": "全量序列见 records.jsonl.gz（单条 JSON 记录）；"
                 "排序负载为倍增后缀数组的复合键，由 sufarr 模式生成"}])

    meta = {
        "scn": "gene",
        "name": "基因组后缀数组",
        "domain": "计算生物学/数据压缩 —— 题目原文点名的排序关键子问题领域",
        "source": SRC_URL,
        "downloaded": "2026-09-28",
        "keyPack": "倍增轮复合键 key = ((rank[i]<<32|rank[i+k])<<23)|i（sufarr 模式生成）",
        "attrBits": 0, "subBits": 23,
        "entityLabel": "轮次", "subLabel": "键域", "attrLabel": "",
        "countLabel": "比较/移动计数", "valLabel": "游程数",
        "note": "无实体台账场景：排序负载与统计由 sufarr 模式生成，UI 读 gene_stats.json",
    }
    write_meta(outdir, meta)

    write_stats(outdir, [
        f"dataset = E. coli K-12 MG1655（NCBI RefSeq {SRC_URL}）",
        f"header = {header}",
        f"length = {n} bases (+1 '$' sentinel = {n+1})",
        f"composition = A:{comp['A']} C:{comp['C']} G:{comp['G']} T:{comp['T']} N:{comp['N']}",
        "GC 含量 = %.2f%%" % (100.0 * (comp['G'] + comp['C']) /
                              max(1, comp['A'] + comp['C'] + comp['G'] + comp['T'])),
        "排序负载: 倍增法 23 轮 × (rank[i],rank[i+k]) 复合键，排序占构造耗时 ~90%",
        "运行 sort_demo_app sufarr app/data/gene 生成 sa.dat / rounds.csv / postings.dat",
    ])
    print(f"[gene] 完成: n={n} (GC {comp['G']+comp['C']}/{n})")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "../downloads/gene/ecoli.fna.gz"
    main(src)
