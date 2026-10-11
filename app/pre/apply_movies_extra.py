#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_movies_extra.py —— 把补充影片落到 MovieLens 数据目录，供 CineRank 直接读取。

产出两样东西，各自职责清晰：
  1) movies.dat        ← 追加补充影片行（id \\t year \\t primaryGenre \\t title）
  2) movies_extra.tsv  ← 补充影片的关注度/好评度（id \\t count \\t avgX10）

**为什么不往 postings.dat 里塞伪造键**：
postings.dat 是 MovieLens 32M 的**真实**评分键（3,200 万条），是本项目「真实数据」
这一说法的凭据。补充影片没有 MovieLens 评分记录，所以它们的关注度不放进 postings，
而是单列一张表（movies_extra.tsv）。C++ 侧在扫描完真实 postings 后叠加这张表，
并在 stats.txt / 界面上说明这批影片的口径来源。这样：
  * postings.dat 永远是纯 MovieLens 数据，可独立校验；
  * 补充影片的票数来源（IMDb 评价人数 ÷ 校准系数）可追溯、可复核；
  * 想只看「纯 MovieLens」时，删掉 movies_extra.tsv 即可。

幂等：重复执行不会重复追加（先按 id 去重再写）。

用法：
    python app/pre/apply_movies_extra.py                 # 应用
    python app/pre/apply_movies_extra.py --revert        # 撤销（恢复备份）
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # app/
PROJ = os.path.dirname(ROOT)
DATA = os.path.join(ROOT, "data")
SRC = os.path.join(PROJ, "_build_tmp", "movies_extra.json")
MOVIES = os.path.join(DATA, "movies.dat")
EXTRA_TSV = os.path.join(DATA, "movies_extra.tsv")
BACKUP = os.path.join(PROJ, "_build_tmp", "movies.dat.bak")

MARK = "# movies_extra: 补充影片（非欧美片源 + 2024-2025 新片）"   # 便于人工识别


def revert():
    if not os.path.exists(BACKUP):
        print(f"[revert] 没有备份 {BACKUP}，无法恢复")
        return 1
    shutil.copy2(BACKUP, MOVIES)
    if os.path.exists(EXTRA_TSV):
        os.remove(EXTRA_TSV)
    print(f"[revert] 已从备份恢复 {MOVIES}，并删除 {EXTRA_TSV}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true", help="从备份恢复 movies.dat")
    a = ap.parse_args()
    if a.revert:
        return revert()

    if not os.path.exists(SRC):
        print(f"[错误] 缺少 {SRC}")
        print("  请先运行：python app/pre/_fetch_movies_extra.py")
        return 1
    extra = json.load(open(SRC, encoding="utf-8"))
    if not extra:
        print("[错误] 补充数据为空")
        return 1

    # 首次应用前备份（幂等：已备份则不覆盖，避免把已改过的文件当原始备份）
    if not os.path.exists(BACKUP):
        shutil.copy2(MOVIES, BACKUP)
        print(f"[1/3] 已备份原始 movies.dat → {BACKUP}")

    # ---------- 读现有 id，去重 ----------
    have = set()
    with open(MOVIES, encoding="utf-8") as f:
        for ln in f:
            tab = ln.find("\t")
            if tab > 0:
                try:
                    have.add(int(ln[:tab]))
                except ValueError:
                    pass
    new = [o for o in extra if o["movieId"] not in have]
    print(f"[2/3] 补充数据 {len(extra)} 部，其中新增 {len(new)} 部"
          f"（已存在 {len(extra) - len(new)} 部跳过）")

    # ---------- 追加 movies.dat ----------
    # 注意：C++ 侧扫描时会对同样 id 的行取第一条/最后一条？—— movieId 是登录键，
    # 这里靠上面的去重保证不出现重复 id，避免歧义。
    with open(MOVIES, "a", encoding="utf-8") as w:
        for o in new:
            title = o["title"].replace("\t", " ")
            w.write(f"{o['movieId']}\t{o['year']}\t{o['primary']}\t{title}\n")
    print(f"       movies.dat 追加 {len(new)} 行")

    # ---------- 写 movies_extra.tsv（关注度/好评度） ----------
    # 格式：movieId \t count \t avgX10 \t imdbVotes \t origin \t groups
    # 前三列 C++ 直接读；后三列是溯源信息（人可读，程序忽略）。
    with open(EXTRA_TSV, "w", encoding="utf-8") as w:
        w.write("# movieId\tcount\tavgX10\timdbVotes\torigin\tgroups\n")
        w.write("# count = IMDb 评价人数 ÷ 20.5（两库重叠样本中位比值，见 _fetch_movies_extra.py）\n")
        for o in new:
            avgx10 = int(round(o["mean"] * 10))
            groups = ",".join(o.get("groups") or []) or "-"
            w.write(f"{o['movieId']}\t{o['count']}\t{avgx10}\t"
                    f"{o['imdbVotes']}\t{o['origin']}\t{groups}\n")
    print(f"       {EXTRA_TSV} 写入 {len(new)} 行")

    nw = sum(1 for o in new if o["origin"] == "non-western")
    rc = sum(1 for o in new if o["origin"] == "recent")
    yrs = {}
    for o in new:
        yrs[o["year"]] = yrs.get(o["year"], 0) + 1
    recent24 = yrs.get(2024, 0)
    recent25 = yrs.get(2025, 0)
    print(f"[3/3] 完成：非欧美 {nw} 部 · 2024 年 {recent24} 部 · 2025 年 {recent25} 部")
    print("       提示：重启应用后生效；撤销用 --revert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
