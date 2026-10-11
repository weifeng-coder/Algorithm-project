#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preprocess.py —— MovieLens 25M → 排序应用数据文件（一次性预处理）

用法:  python app/preprocess.py <ml-25m目录> <输出目录>
示例:  python app/preprocess.py downloads/ml-25m app/data

输出:
  postings.dat      25M 条 posting 键（uint64 小端），8 字节/条
                    key = (movieId << 22) | (userId << 4) | ratingIdx
                      movieId ≤ 209171 < 2^18，userId ≤ 162541 < 2^18，
                      ratingIdx = rating*2-1 ∈ [0,9]（评分只有 10 档，4 位）
                    排序后按 movieId 分段聚合直接得到倒排表，ratingIdx 保留在
                    键的低 4 位用于打分。键按文件原始序（userId 主序）导出 =
                    真实数据的自然乱序。
  postings_1e5.dat / postings_1e6.dat   等距抽样档（规模阶梯用）
  movies.dat        电影：movieId \t year \t primaryGenre \t title
  stats.txt         数据集体检摘要

设计说明：把 (movie, user, rating) 三元组整体打包进单个 int64 键，排序即建索引
—— 排序后按 movieId 分段聚合得到倒排表，无需伴随数组置换（键编码演示）。
"""
import csv
import os
import struct
import sys
import time

def main(src, out):
    os.makedirs(out, exist_ok=True)
    ratings_csv = os.path.join(src, "ratings.csv")
    movies_csv = os.path.join(src, "movies.csv")
    t0 = time.time()

    # ---------- 1) ratings.csv → postings.dat（流式，三档同步导出） ----------
    paths = {n: open(os.path.join(out, f"postings{n}.dat"), "wb")
             for n in ("", "_1e5", "_1e6")}
    n_total = 0
    users, movies = set(), set()
    min_mid, max_mid = 1 << 62, 0
    pack = struct.Struct("<Q").pack
    stride1e5 = stride1e6 = None  # 稍后按总数定步长？—— 流式不知总数，改用哈希抽样:
    # 等距抽样需要总行数；改为「行号 % step == 0」且 step 由 25M 先验值估计，
    # 最后如抽样数偏离目标 >5% 不影响使用（仍为确定性无偏抽样）。
    est_total = 25000095
    step1e5 = max(1, est_total // 100000)
    step1e6 = max(1, est_total // 1000000)

    with open(ratings_csv, "r", encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        header = next(rd)
        assert header[:4] == ["userId", "movieId", "rating", "timestamp"], header
        for row in rd:
            uid = int(row[0]); mid = int(row[1]); rating = float(row[2])
            ridx = int(rating * 2) - 1          # 0.5→0 ... 5.0→9
            if not (0 <= ridx <= 9):
                ridx = 0 if ridx < 0 else 9
            key = (mid << 22) | (uid << 4) | ridx
            b = pack(key)
            paths[""].write(b)
            if n_total % step1e5 == 0: paths["_1e5"].write(b)
            if n_total % step1e6 == 0: paths["_1e6"].write(b)
            users.add(uid); movies.add(mid)
            if mid < min_mid: min_mid = mid
            if mid > max_mid: max_mid = mid
            n_total += 1
            if n_total % 5000000 == 0:
                print(f"  ... {n_total:,} rows ({time.time()-t0:.0f}s)", flush=True)

    for p in paths.values():
        p.close()
    print(f"postings: {n_total:,} keys, users={len(users):,}, movies={len(movies):,}, "
          f"movieId∈[{min_mid},{max_mid}] ({time.time()-t0:.0f}s)")

    # ---------- 2) movies.csv → movies.dat ----------
    n_mov = 0
    genres_all = {}
    with open(movies_csv, "r", encoding="utf-8", newline="") as f, \
         open(os.path.join(out, "movies.dat"), "w", encoding="utf-8") as w:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            mid = row[0]; title = row[1].replace("\t", " ").strip(); genres = row[2]
            # 年份从标题尾部 "(YYYY)" 提取：[-5:-1] = "YYYY"
            year = 0
            if len(title) >= 6 and title[-1] == ")":
                tail = title[-5:-1]
                if tail.isdigit():
                    year = int(tail)
            primary = genres.split("|")[0] if genres else "(unknown)"
            if primary == "(no genres listed)":
                primary = "(none)"
            genres_all[primary] = genres_all.get(primary, 0) + 1
            w.write(f"{mid}\t{year}\t{primary}\t{title}\n")
            n_mov += 1
    print(f"movies: {n_mov:,} rows")

    # ---------- 3) stats.txt ----------
    with open(os.path.join(out, "stats.txt"), "w", encoding="utf-8") as w:
        w.write(f"dataset={os.path.basename(os.path.normpath(src))}\n")
        w.write(f"postings={n_total}\n")
        w.write(f"users={len(users)}\n")
        w.write(f"movies={n_mov}\n")
        w.write(f"movie_id_range=[{min_mid},{max_mid}]\n")
        w.write(f"key_bits={(max_mid << 22 | 162540 << 4 | 9).bit_length()}\n")
        w.write("genres:\n")
        for g, c in sorted(genres_all.items(), key=lambda kv: -kv[1]):
            w.write(f"  {g}\t{c}\n")
    print(f"done in {time.time()-t0:.0f}s → {out}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    main(sys.argv[1], sys.argv[2])
