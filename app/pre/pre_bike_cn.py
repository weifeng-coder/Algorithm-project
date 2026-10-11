#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国内骑行场景（北京 · 2017 摩拜杯算法挑战赛订单数据）预处理。

数据源：摩拜杯 2017 训练数据（2017-05-10 ~ 05-24 北京城区，~320 万单，
真实起终点经纬度）。GitHub 公开镜像匿名直下：
  https://raw.githubusercontent.com/only-changer/Mobike-Cup/master/work/data{N}.csv
  N=0..14 ↔ 日期 2017-05-(10+N)（按官方连续 15 天推断；镜像中 data7 为 0 字节，
  对应 05-17 缺失，stats 如实记录）。无表头六列：
  orderid, start_time(H:MM:SS), start_lat, start_lon, end_lat, end_lon

自由流单车没有固定站点 → 实体 = 起点 ~500m 网格（0.005°）；
键位与 bike 场景同构：key = gridDense<<21 | minuteOfYear (attrBits=0, subBits=21)，
排序内核与 UI 框架零改动。OD 对（起点→终点网格）另存 od.json —— 这是
Citi Bike 版本做不了的面板（其键里没有终点）。

用法：python app/pre/pre_bike_cn.py <data目录>
"""
import csv
import glob
import json
import os
import sys
from array import array
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import write_meta, write_sample, write_stats, write_tiers  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "bikecn")
CELL = 0.005                       # ~500m 网格
CUMDAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]  # 2017 非闰年
DAY0 = 10                          # data0 = 2017-05-10（周三）
WK = 6                             # 2017-01-01 是周日（周一=0 记 6）
BBOX = (39.4, 40.1, 116.0, 117.0)  # 北京城区+近郊包围盒（滤 GPS 漂移脏点）


def in_bbox(lat, lon):
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def mty_of(day, hms):
    try:
        hh, mm, ss = hms.split(":")
        sec = int(hh) * 3600 + int(mm) * 60 + int(ss)
        return (CUMDAYS[4] + day - 1) * 1440 + sec // 60
    except (ValueError, IndexError):
        return -1


def main(srcdir):
    files = sorted(glob.glob(os.path.join(srcdir, "data*.csv")),
                   key=lambda p: int("".join(ch for ch in os.path.basename(p) if ch.isdigit())))
    if not files:
        sys.exit(f"{srcdir} 下没有 data*.csv")

    grids = {}                    # (glat, glon) → dense id（首次出现序，确定可复现）
    keys = array("q")             # postings：文件序 = 日期序 = 时间序
    ledger_parts = []
    od = Counter()
    heat = [[0] * 24 for _ in range(7)]
    day_rows = {}
    sample = []
    total = 0
    skipped = 0

    for fp in files:
        n_day = int(os.path.getsize(fp))
        if n_day == 0:
            print(f"{os.path.basename(fp)}: 0 字节（镜像缺失该天），跳过")
            day_rows[os.path.basename(fp)] = 0
            continue
        idx = int("".join(ch for ch in os.path.basename(fp) if ch.isdigit()))
        day = DAY0 + idx
        dk = []
        with open(fp, encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f):
                if len(row) < 6:
                    continue
                m = mty_of(day, row[1])
                if m < 0:
                    continue
                try:
                    slat, slon, elat, elon = (float(row[2]), float(row[3]),
                                              float(row[4]), float(row[5]))
                except ValueError:
                    continue
                if not (in_bbox(slat, slon) and in_bbox(elat, elon)):
                    skipped += 1
                    continue
                g = grids.setdefault((round(slat / CELL), round(slon / CELL)), len(grids))
                keys.append((g << 21) | m)
                dk.append((g << 21) | m)
                e = grids.setdefault((round(elat / CELL), round(elon / CELL)), len(grids))
                od[(g, e)] += 1
                w = (CUMDAYS[4] + day - 1 + WK) % 7
                heat[w][m % 1440 // 60] += 1
                if len(sample) < 200:
                    sample.append({"s": [slat, slon], "e": [elat, elon], "m": m})
                total += 1
        ledger_parts.append(dk)
        day_rows[f"05-{day:02d}"] = len(dk)
        print(f"{os.path.basename(fp)} = 05-{day:02d}: 累计 {total}")

    gcount = Counter(k >> 21 for k in keys)
    ents = []
    for (glat, glon), d in sorted(grids.items(), key=lambda kv: kv[1]):
        ents.append({"id": d, "lat": round(glat * CELL, 5), "lon": round(glon * CELL, 5),
                     "n": gcount.get(d, 0)})
    with open(os.path.join(OUT, "entities.dat"), "w", encoding="utf-8", newline="\n") as f:
        for e in ents:
            f.write(f'{e["id"]}\t0\t(grid)\t0\t网格{e["id"]}（{e["lat"]}, {e["lon"]}）\n')
    with open(os.path.join(OUT, "gridgeo.json"), "w", encoding="utf-8") as f:
        json.dump({"grids": {str(e["id"]): {"lat": e["lat"], "lon": e["lon"], "n": e["n"]}
                             for e in ents if e["n"] > 0},
                   "meta": {"cell_deg": CELL, "note": "自由流单车无固定站点，~500m 起点网格做实体"}},
                  f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, "od.json"), "w", encoding="utf-8") as f:
        json.dump({"pairs": [{"s": s, "e": e, "n": n} for (s, e), n in od.most_common(200)]},
                  f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, "cnheat.json"), "w", encoding="utf-8") as f:
        json.dump({"matrix": heat, "total": sum(map(sum, heat)),
                   "days": day_rows,
                   "note": "星期×小时=2017 年历（05-10 是周三）；OD 与坐标来自真实起终点，无插值"},
                  f, ensure_ascii=False, separators=(",", ":"))

    write_tiers(OUT, keys)
    ledger = array("q")
    for part in ledger_parts:
        ledger.extend(part)
    with open(os.path.join(OUT, "ledger.dat"), "wb") as f:
        f.write(ledger.tobytes())
    write_sample(os.path.join(OUT, "records_sample.json"), sample)

    days = sorted(day_rows)
    meta = {
        "scn": "bikecn",
        "name": "城市骑行调度 · 北京（国内）",
        "domain": "城市出行/调度 —— 国内真实骑行数据（2017 摩拜杯算法挑战赛，北京城区）",
        "source": "https://github.com/only-changer/Mobike-Cup（摩拜杯 2017 训练数据公开镜像）",
        "downloaded": "2026-10-02",
        "keyPack": "key = gridDense<<21 | minuteOfYear（32 位）",
        "attrBits": 0,
        "subBits": 21,
        "entityLabel": "起点网格(~500m)",
        "subLabel": "年内心分钟",
        "attrLabel": "",
        "countLabel": "骑行量",
        "valLabel": "—",
        "rankModes": {"pop": "热度（骑行量↓）"},
        "months_covered": [5],
        "days_note": "2017-05-10 ~ 05-24（data7=05-17 镜像缺失，0 字节）",
    }
    write_meta(OUT, meta)
    write_stats(OUT, [
        f"dataset = 北京摩拜 2017-05-10~05-24（摩拜杯算法挑战赛训练数据，GitHub 公开镜像）",
        f"orders = {total}  grids(~500m) = {len(grids)}  有效天 = {len([v for v in day_rows.values() if v])}/15（05-17 缺失）",
        f"outliers_skipped = {skipped}（起/终点越出北京包围盒 lat 39.4-40.1, lon 116.0-117.0 的 GPS 漂移脏点，如实剔除）",
        "postings.dat = 订单时间序键 gridDense<<21|minuteOfYear",
        "od.json / cnheat.json / gridgeo.json = 起终点网格对、星期×小时热力、网格坐标（真实数据派生）",
        "坐标为原始经纬度（未做 GCJ-02 纠偏），落点精度 ~百米级，对网格聚合无影响",
    ])
    print(f"完成：{total} 单 / {len(grids)} 网格 → {OUT}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "..", "..", "downloads", "mobike")
    os.makedirs(OUT, exist_ok=True)
    main(src)
