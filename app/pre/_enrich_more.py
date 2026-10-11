#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二轮增强：从随包抽样键派生 UI 剩余面板的数据（全部离线，不需要原始数据）。

产出：
  data/movie/moviehist.json      单片评分分布（Top 影片 10 档直方图，点击榜单联动）
  data/bike/bikehourweek.json    通勤热力 7×24（星期×小时）+ 月份覆盖
  data/flight/flightdelay.json   机场×延误档矩阵（准点率分组/机场倒排）+ 日期×延误序列

口径与 pre_*.py 一致：
  movie  key = mid<<22 | uid<<4 | ridx      ridx∈[0,9]（10 档，rating=(ridx+1)/2）
  bike   key = stationId<<21 | minuteOfYear
  flight key = originDense<<25 | minuteOfYear<<4 | delayIdx   2019 非闰年
"""
import json
import os
import struct
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CUMDAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]  # 2019 非闰年


def read_keys(path):
    raw = open(path, "rb").read()
    n = len(raw) // 8
    return struct.unpack(f"<{n}Q", raw[: n * 8])


def dump(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(os.path.basename(path), os.path.getsize(path))


def movie():
    """单片评分分布：影片 10 档直方图（优先全量 postings.dat，否则用 1e6 抽样）。"""
    titles = {}
    with open(os.path.join(DATA, "movies.dat"), encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 4:
                titles[int(p[0])] = {"title": p[3], "year": int(p[1]) if p[1] else None,
                                     "genre": p[2]}
    per = defaultdict(lambda: [0] * 10)
    full_path = os.path.join(DATA, "postings.dat")
    use_full = os.path.exists(full_path)
    keys = read_keys(full_path if use_full else os.path.join(DATA, "postings_1e6.dat"))
    for k in keys:
        per[k >> 22][k & 15] += 1

    # 入选 = 条数 Top-1200 ∪ 三种排序键各自前 300。
    # 只用条数 Top-N 会让近年影片全部落选（新片票数天然少），
    # 于是在榜单上点开新片就没有直方图可看。
    pick = {mid for mid, _ in sorted(per.items(), key=lambda kv: -sum(kv[1]))[:1200]}
    cand = []
    for mid, h in per.items():
        c = sum(h)
        if c <= 0:
            continue
        mean = (sum(i * v for i, v in enumerate(h)) / c + 1.0) / 2.0
        cand.append((mid, c, int(mean * 10 + 0.5)))
    if cand:
        cnts = sorted(c for _, c, _ in cand)
        mprev = cnts[len(cnts) // 2] or 1                  # 近似全局票数中位数
        gmean = (sum(c for _, c, _ in cand) / len(cand) + 1.0) / 2.0
        pick |= {mid for mid, c, a in sorted(cand, key=lambda t: (t[1], t[2]), reverse=True)[:300]}

        # 均分类/加权类的候选必须先过票数门槛，否则会被大量「几票 5.0★」的
        # 冷门片塞满前 300，真正的高票佳作反而落选（UI 默认门槛 = 1000 票）。
        filt = [t for t in cand if t[1] >= 1000] or cand
        pick |= {mid for mid, c, a in sorted(filt, key=lambda t: (t[2], t[1]), reverse=True)[:300]}

        def _w(t):
            mid, c, a = t
            w = c / (c + mprev)
            return w * (a / 10.0) + (1 - w) * gmean         # 加权评分主键（同 bayes 口径）
        pick |= {mid for mid, c, a in sorted(filt, key=_w, reverse=True)[:300]}

    rows = [(mid, per[mid]) for mid in pick if mid in per]
    movies = {}
    for mid, h in rows:
        c = sum(h)
        t = titles.get(mid)
        if not t:
            continue
        mean = (sum(i * v for i, v in enumerate(h)) / c + 1.0) / 2.0
        movies[str(mid)] = {"title": t["title"], "year": t["year"], "genre": t["genre"],
                            "hist": h, "count": c, "mean": round(mean, 3)}
    # 全量条数从 stats.txt 读，避免换数据集（ml-25m → ml-32m）后写死值失真
    full = 25000095
    try:
        with open(os.path.join(DATA, "stats.txt"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("postings="):
                    full = int(line.split("=", 1)[1].strip())
                    break
    except Exception:
        pass
    if use_full:
        out = {
            "sample": len(keys),
            "full_postings": full,
            "scale": 1.0,
            "movies": movies,
            "note": ("来自全量 postings.dat，评分条数为精确值；入选 = 条数 Top-1200 "
                     "∪ 关注度/好评度/评分优先三种排序键各前 300（保证近年影片也有直方图）"),
        }
    else:
        sc = round(full / len(keys), 1)
        out = {
            "sample": len(keys),
            "full_postings": full,
            "scale": sc,
            "movies": movies,
            "note": ("来自 postings_1e6.dat 等距抽样（约 1/%.0f）；评分条数 ×%.0f ≈ 全量，"
                     "分布形状与全量一致") % (sc, sc),
        }
    dump(os.path.join(DATA, "movie", "moviehist.json"), out)


def bike():
    """通勤热力：星期×小时矩阵 + 月份覆盖（星期一=0）。2019-01-01 是周二。"""
    per = defaultdict(lambda: defaultdict(int))   # (weekday, hour) -> n
    month = defaultdict(int)
    keys = read_keys(os.path.join(DATA, "bike", "postings_1e6.dat"))
    for k in keys:
        mty = k & ((1 << 21) - 1)
        d, rem = divmod(mty, 1440)
        hh = rem // 60
        per[(d + 1) % 7][hh] += 1                  # (d+1)%7: 周一=0（1/1/2019 周二）
        m = next(c for c in range(11, -1, -1) if CUMDAYS[c] <= d)
        month[m] += 1
    matrix = [[per[w][h] for h in range(24)] for w in range(7)]
    total = sum(sum(r) for r in matrix)
    out = {
        "matrix": matrix,
        "total": total,
        "sample": len(keys),
        "full": 12787418,
        "months": [[m + 1, month.get(m, 0)] for m in range(12)],
        "months_note": "骑行数据仅 1–9 月（S3 年度 zip 实际分片），10–12 月无数据，不插值",
        "weekday_of": "2019-01-01 是周二；(dayOfYear+1)%7，0=周一",
        "note": "来自 postings_1e6.dat 等距抽样；时段=出发时刻（键用 start station id）",
    }
    dump(os.path.join(DATA, "bike", "bikehourweek.json"), out)


def flight():
    """机场×延误档矩阵 + 月/日聚合。准点 = delayIdx ≤ 2（≤15 分钟）。"""
    iata = {}
    geo = json.load(open(os.path.join(DATA, "flight", "flightgeo.json"), encoding="utf-8"))
    for code, g in geo.get("airports", {}).items():
        iata[g["id"]] = code
    per = defaultdict(lambda: [0] * 10)
    month = defaultdict(lambda: [0, 0, 0])         # n, ontime_n, sum_didx
    date = defaultdict(lambda: [0, 0])             # n, sum_didx
    keys = read_keys(os.path.join(DATA, "flight", "postings_1e6.dat"))
    for k in keys:
        oid = k >> 25
        mty = (k >> 4) & ((1 << 21) - 1)
        didx = k & 15
        per[oid][didx] += 1
        d, rem = divmod(mty, 1440)
        m = next(c for c in range(11, -1, -1) if CUMDAYS[c] <= d)
        mi = month[m]
        mi[0] += 1
        mi[1] += didx <= 2
        mi[2] += didx
        di = date[d]
        di[0] += 1
        di[1] += didx
    airports = {}
    for oid, h in per.items():
        code = iata.get(oid)
        if not code:
            continue
        c = sum(h)
        airports[code] = {
            "count": c,
            "ontime": round(sum(h[:3]) / c, 4),
            "mean": round(sum(i * v for i, v in enumerate(h)) / c, 3),
            "h": h,
        }
    dates = sorted(
        ({"d": d + 1, "n": v[0], "avg": round(v[1] / v[0], 3)} for d, v in date.items()),
        key=lambda x: x["d"])
    # 键位实验台用：等距取 1200 条原始键（o=机场稠密id, m=年内心分钟, d=延误档）
    step = len(keys) / 1200
    kl = [{"o": k >> 25, "m": (k >> 4) & ((1 << 21) - 1), "d": k & 15}
          for k in (keys[int(i * step)] for i in range(1200))]
    out = {
        "sample": len(keys),
        "full": 2417022,
        "ontime_def": "准点 = delayIdx ≤ 2（≤15 分钟，业界常用口径）",
        "airports": airports,
        "months": [
            {"k": "2019-%02d" % (m + 1), "n": v[0],
             "ontime": round(v[1] / v[0], 4), "mean": round(v[2] / v[0], 3)}
            for m, v in sorted(month.items())
        ],
        "dates": dates,
        "keys": kl,
        "note": ("来自 postings_1e6.dat 等距抽样（约 41% 航班）；航司维度在 ledger.dat 键里"
                 "（未随包）， Dest 终点不在 postings 键里 —— 航线图/航司视角需要全量数据"),
    }
    dump(os.path.join(DATA, "flight", "flightdelay.json"), out)


if __name__ == "__main__":
    movie()
    bike()
    flight()
