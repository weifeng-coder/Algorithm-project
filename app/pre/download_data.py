#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""download_data.py —— 六场景原始数据一键下载（断点续传 + 完整性校验）

所有数据集均为公开直链，逐一实测验证（2026-09-28/29）。本脚本下载到
工程根目录的上一级 downloads/<场景>/（仓库外，.gitignore 已排除），
随后由各 pre_<scn>.py 完成预处理。 teammates 克隆仓库后只需：

    python app/pre/download_data.py            # 全量
    python app/pre/download_data.py --only web gene   # 按场景
    python app/pre/download_data.py --proxy http://127.0.0.1:7897

特性：
  - 断点续传（Range 续传 + 目标字节数校验，中断后重跑自动继续）
  - 魔数校验（zip=PK、gzip=1f 8b；BTS 这类"返回 200 + HTML 错误页"的
    源靠它识别，坏文件自动删除重试）
  - 可选代理（Clash/V2Ray 常见端口；不传则直连）
  - 航班 2-9 月在本次实验期间持续不可达（服务器返回 200+错误页），
    脚本会如实报告失败而不阻塞其余场景（4 个月 2.4M 行已够用）。
"""

import argparse
import gzip
import os
import sys
import time
import urllib.request
import zipfile

# 工程根（app/ 的上一级）；下载目录在其上一级 downloads/（仓库外）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DL = os.path.join(os.path.dirname(ROOT), "downloads")

NCBI = ("https://ftp.ncbi.nlm.nih.gov/genomes/refseq/bacteria/Escherichia_coli/"
        "reference/GCF_000005845.2_ASM584v2/GCF_000005845.2_ASM584v2_genomic.fna.gz")
BTS = ("https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_"
       "On_Time_Performance_1987_present_2019_{}.zip")

# key → (说明, [(url, 相对downloads路径, 期望字节数或0), ...], 解压动作)
DATASETS = {
    "movie": ("MovieLens 25M 评分（movie 场景，262MB）", [
        ("https://files.grouplens.org/datasets/movielens/ml-25m.zip",
         "ml-25m.zip", 262198027),
    ], "unzip_ml25m"),
    "amazon": ("Amazon Electronics 评论（SNAP，318MB）", [
        ("https://snap.stanford.edu/data/amazon/productGraph/"
         "categoryFiles/ratings_Electronics.csv",
         "amazon/ratings_Electronics.csv", 318766497),
    ], None),
    "bike": ("Citi Bike 2019 骑行日志（S3 全年包 858MB，实际含 1-9 月）", [
        ("https://s3.amazonaws.com/tripdata/2019-citibike-tripdata.zip",
         "bike/2019-citibike-tripdata.zip", 858704099),
    ], None),
    "web": ("Stanford web-Google 网页图（21MB）", [
        ("https://snap.stanford.edu/data/web-Google.txt.gz",
         "web/web-Google.txt.gz", 21168784),
    ], None),
    "flight": ("美国 DOT 2019 航班准点（每月 30-70MB；2-9 月曾长期不可达）", [
        (BTS.format(f"{m:02d}"), f"flight/2019_{m:02d}.zip", 0)
        for m in range(1, 13)
    ], None),
    "gene": ("E. coli K-12 基因组（NCBI RefSeq，1.4MB）", [
        (NCBI, "gene/ecoli.fna.gz", 1379902),
    ], None),
}

MAGIC = {".zip": b"PK\x03\x04", ".gz": b"\x1f\x8b"}


def opener_with(proxy):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()   # 无代理：禁用环境代理干扰，直连


def head_size(op, url):
    req = urllib.request.Request(url, method="HEAD")
    try:
        with op.open(req, timeout=30) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def looks_valid(path, magic):
    if magic is None:
        return os.path.getsize(path) > 0
    with open(path, "rb") as f:
        return f.read(len(magic)) == magic


def fetch(op, url, dest, expect, magic, tries=10):
    """断点续传下载 + 校验；成功返回 True"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for attempt in range(1, tries + 1):
        cur = os.path.getsize(dest) if os.path.exists(dest) else 0
        if expect and cur >= expect:
            return True
        if cur and magic and not looks_valid(dest, magic):
            cur = 0
            os.remove(dest)          # 半截文件且魔数不对 → 推倒重来
        req = urllib.request.Request(url)
        if cur:
            req.add_header("Range", f"bytes={cur}-")
        try:
            with op.open(req, timeout=90) as resp:
                data = resp.read()
                mode = "ab" if (cur and resp.status == 206) else "wb"
                if mode == "wb":
                    cur = 0
                with open(dest, mode) as f:
                    f.write(data)
        except Exception as e:
            print(f"    attempt {attempt}: {type(e).__name__}: {e}")
        size = os.path.getsize(dest) if os.path.exists(dest) else 0
        if (not expect or size >= expect) and looks_valid(dest, magic) and size > 0:
            return True
        if size and magic and not looks_valid(dest, magic):
            os.remove(dest)          # 200+错误页 → 删掉重试
        time.sleep(min(2 * attempt, 10))
    return os.path.exists(dest) and looks_valid(dest, magic) and \
        (not expect or os.path.getsize(dest) >= expect)


def unzip_ml25m():
    """movie 场景：解压 ml-25m.zip（app/preprocess.py 需要 CSV）"""
    z = os.path.join(DL, "ml-25m.zip")
    if not os.path.exists(z):
        return
    mark = os.path.join(DL, "ml-25m", "ratings.csv")
    if os.path.exists(mark):
        print("  ml-25m 已解压，跳过")
        return
    print("  解压 ml-25m.zip ...")
    with zipfile.ZipFile(z) as zf:
        zf.extractall(DL)


def human(n):
    for u in ("", "K", "M", "G"):
        if abs(n) < 1024:
            return f"{n:.1f}{u}" if u else str(int(n))
        n /= 1024
    return f"{n:.1f}G"


def main():
    ap = argparse.ArgumentParser(description="六场景原始数据一键下载")
    ap.add_argument("--proxy", default=None, help="如 http://127.0.0.1:7897")
    ap.add_argument("--only", default="", help="逗号分隔场景: movie,amazon,bike,web,flight,gene")
    ap.add_argument("--tries", type=int, default=10)
    args = ap.parse_args()

    targets = [k for k in DATASETS if not args.only or k in args.only.split(",")]
    op = opener_with(args.proxy)
    fails = []
    for key in targets:
        desc, items, action = DATASETS[key]
        print(f"\n==== {key}: {desc} ====")
        ok = True
        for url, rel, expect in items:
            dest = os.path.join(DL, rel.replace("/", os.sep))
            size = os.path.getsize(dest) if os.path.exists(dest) else 0
            magic = MAGIC.get(os.path.splitext(rel)[1])
            if size and (not expect or size >= expect) and looks_valid(dest, magic):
                print(f"  已存在（{human(size)}），跳过: {rel}")
                continue
            if not expect:
                expect = head_size(op, url)
                # BTS 对 HEAD 也可能返回错误页长度；靠魔数校验兜底
                if magic and expect and expect < 5_000_000:
                    expect = 0
            print(f"  下载 {rel}（期望 {human(expect) if expect else '未知'}）...")
            if fetch(op, url, dest, expect, magic, args.tries):
                print(f"  OK: {human(os.path.getsize(dest))} -> {rel}")
            else:
                print(f"  !! 失败: {url}")
                fails.append(rel)
                ok = False
        if ok and action == "unzip_ml25m":
            unzip_ml25m()

    print("\n==== 汇总 ====")
    if fails:
        print("以下文件未成功（可重跑本脚本续传）：")
        for f in fails:
            print("  -", f)
        return 1
    print("全部就绪。下一步：python app/pre/prepare_data.bat 或逐个运行 pre_<scn>.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
