#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_fetch_ml32m.py —— 可选：把电影场景从 MovieLens 25M 升级到 ml-32m

背景：当前 movie 场景用 GroupLens MovieLens 25M，其评分收录**截止 2019 年**，
所以「关注度」榜单里不会出现 2020 年以后的电影（按评分条数排序，新片票数天然少）。
GroupLens 后续发布的 ml-32m（3200 万条评分）收录到 2023 年，可覆盖近年影片。

本脚本只负责把 ml-32m 下下来并解压到 downloads/ml-32m，之后重跑既有预处理即可：

    python app/pre/_fetch_ml32m.py              # 直连
    python app/pre/_fetch_ml32m.py --proxy http://127.0.0.1:7890
    python app/preprocess.py downloads/ml-32m app/data

数据契约（movies.dat / postings.dat / postings_1e5|1e6.dat / meta / stats）与
排序内核完全不变，前端与各场景 API 零改动——这正是本项目「换数据不换代码」的设计点。

说明：ml-32m 与 ml-25m 的 movieId 体系一致（同一 MovieLens 词表），因此
frontend/assets/posters/<movieId>.jpg 的海报缓存大体可复用；新片海报可重跑
app/pre/_fetch_posters.py 补齐。
"""
import argparse
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # app/
PROJ = os.path.dirname(ROOT)                                          # 工程根
DL = os.path.join(PROJ, "downloads")

URL = "https://files.grouplens.org/datasets/movielens/ml-32m.zip"
ZIP = "ml-32m.zip"
UA = {"User-Agent": "CineRank-course/1.0 (educational; dataset fetch)"}


def opener(proxy):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def download(op, dest, retries=8):
    """流式下载到 dest；已存在且能打开为合法 zip 则跳过（幂等）。"""
    if os.path.exists(dest) and zipfile.is_zipfile(dest):
        print(f"  已存在合法压缩包，跳过下载：{dest}")
        return True
    for i in range(retries):
        try:
            r = op.open(urllib.request.Request(URL, headers=UA), timeout=180)
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                while True:
                    c = r.read(1 << 20)
                    if not c:
                        break
                    f.write(c)
                    got += len(c)
                    if total and got % (20 << 20) < (1 << 20):
                        print(f"    {got / 1048576:.0f}/{total / 1048576:.0f} MB", flush=True)
            if total and got < total:
                print(f"  第 {i + 1} 次不完整（{got}/{total}），重试…")
                time.sleep(4)
                continue
            os.replace(tmp, dest)
            print(f"  下载完成：{got / 1048576:.1f} MB")
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"  第 {i + 1} 次失败：{type(e).__name__} {str(e)[:80]}；稍后重试")
            time.sleep(6)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default=None, help="如 http://127.0.0.1:7890")
    a = ap.parse_args()

    os.makedirs(DL, exist_ok=True)
    dest = os.path.join(DL, ZIP)
    op = opener(a.proxy)

    print(f"[ml-32m] 下载源：{URL}")
    if not download(op, dest):
        print("\n[失败] 网络不可达（GroupLens 当前不可访问）。")
        print("  可稍后重跑本脚本；或手动下载后放到：")
        print(f"    {dest}")
        print("  然后执行： python app/preprocess.py downloads/ml-32m app/data")
        return 1

    print(f"[ml-32m] 解压 → {DL}")
    with zipfile.ZipFile(dest) as z:
        z.extractall(DL)
    ratings = os.path.join(DL, "ml-32m", "ratings.csv")
    movies = os.path.join(DL, "ml-32m", "movies.csv")
    if not (os.path.exists(ratings) and os.path.exists(movies)):
        print("[失败] 解压后缺少 ratings.csv / movies.csv")
        return 1

    # 快速体检：影片年份上界，确认近年覆盖。
    # movies.csv 列序为 movieId,title,genres —— 年份在 title 里，但后面还跟着 genres 列，
    # 所以不能用行尾锚定，要按列切分后再取 title。
    import re
    mx, recent = 0, 0
    with open(movies, encoding="utf-8", errors="replace") as f:
        next(f, None)                                  # 跳过表头
        for line in f:
            cols = line.rstrip("\n").split(",")
            if len(cols) < 3:
                continue
            title = ",".join(cols[1:-1])               # title 里可能含逗号
            m = re.search(r"\((\d{4})\)\s*$", title.strip())
            if not m:
                continue
            y = int(m.group(1))
            if y > mx:
                mx = y
            if y >= 2020:
                recent += 1
    print(f"[ml-32m] movies.csv 最新年份={mx}，2020 年后影片={recent} 部")
    print("\n下一步（数据契约不变，直接重跑既有预处理）：")
    print("  python app/preprocess.py downloads/ml-32m app/data")
    print("  # 如需补新片海报：python app/pre/_fetch_posters.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
