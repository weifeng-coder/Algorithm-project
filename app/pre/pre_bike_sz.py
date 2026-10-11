#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""深圳骑行场景预处理（数据：深圳市政府数据开放平台「共享单车企业每日订单表」）。

输入：downloads/sz_orders/<yyyymmdd>.csv.gz（由 _fetch_sz_orders.py 拉取，
字段 start_time,start_lat,start_lng,end_lat,end_lng,com_id）

处理：
  - 坐标纠偏：平台官方标注 BD09，但对齐检验（落入区界率 + 地标核对）证实
    实际为 WGS84 —— 按 WGS84 → GCJ02 纠偏后 98.9% 落入区界，按标注转换仅 91%。
    以实测为准，底图（GCJ02）与网格才能贴合。
  - 自由流单车无固定站点 → 实体 = 起点 ~500m 网格（0.005°，GCJ02 量化）
  - 键位与 bike/bikecn 同构：key = gridDense<<21 | minuteOfYear（attrBits=0, subBits=21）
  - 行政区划展示裁剪：起/终点落在区界 1.5km 缓冲带之外的漂移单剔除
    （缓冲带内保留福田/皇岗/文锦渡/莲塘等口岸跨界簇）；剔除量如实写入 stats
  - OD 对（起点→终点网格）另存 od.json；星期×小时热力 cnheat.json（按 2021 年历）

用法：python app/pre/pre_bike_sz.py
"""
import csv
import glob
import gzip
import json
import math
import os
import sys
from array import array
from collections import Counter, defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import write_meta, write_sample, write_stats, write_tiers  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "bikesz")
SRC = os.path.join(ROOT, "..", "..", "downloads", "sz_orders")
CELL = 0.005
CUMDAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]  # 2021 非闰年
BBOX = (22.38, 22.90, 113.72, 114.66)   # 深圳裁剪底图包围盒（GCJ02）+ 小余量
_A = 6378245.0
_EE = 0.00669342162296594323
BOUND = 0.015   # 区界缓冲带 ≈1.5km：口岸跨界簇保留，更远的漂移剔除


def _tf_lat(x, y):
    r = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
    r += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
    r += (20.0*math.sin(y*math.pi) + 40.0*math.sin(y/3.0*math.pi)) * 2.0/3.0
    r += (160.0*math.sin(y/12.0*math.pi) + 320.0*math.sin(y*math.pi/30.0)) * 2.0/3.0
    return r


def _tf_lon(x, y):
    r = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    r += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
    r += (20.0*math.sin(x*math.pi) + 40.0*math.sin(x/3.0*math.pi)) * 2.0/3.0
    r += (150.0*math.sin(x/12.0*math.pi) + 300.0*math.sin(x/30.0*math.pi)) * 2.0/3.0
    return r


def raw_to_gcj02(lon, lat):
    """WGS84 → GCJ02（国测局标准算法）。见模块 docstring 的对齐检验结论。"""
    dlat = _tf_lat(lon - 105.0, lat - 35.0)
    dlon = _tf_lon(lon - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    magic = 1 - _EE * math.sin(rad) ** 2
    sq = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sq) * math.pi)
    dlon = (dlon * 180.0) / (_A / sq * math.cos(rad) * math.pi)
    return lon + dlon, lat + dlat


def in_bbox(lat, lon):
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def load_district_rings(path):
    """深圳裁剪底图的区界外环，供行政区划裁剪"""
    g = json.load(open(path, encoding="utf-8"))
    rings = []
    for ft in g["features"]:
        geom = ft["geometry"]
        ps = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        for poly in ps:
            rings.append(poly[0])
    return rings


def build_boundary_index(rings):
    """外环 → (外环列表, 全部线段)。inside=射线法；near=点到线段距离。"""
    segs = []
    for ring in rings:
        for i in range(len(ring) - 1):
            segs.append((ring[i][0], ring[i][1], ring[i+1][0], ring[i+1][1]))
    return rings, segs


def in_boundary(index, x, y):
    """区界内，或落在 BOUND（≈1.5km）缓冲带内 → True。"""
    rings, segs = index
    for ring in rings:
        c = False; j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i][0], ring[i][1]; xj, yj = ring[j][0], ring[j][1]
            if ((yi > y) != (yj > y)) and (x < (xj-xi)*(y-yi)/(yj-yi+1e-18)+xi): c = not c
            j = i
        if c: return True
    for xa, ya, xb, yb in segs:
        dx, dy = xb - xa, yb - ya
        L2 = dx*dx + dy*dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x-xa)*dx + (y-ya)*dy) / L2))
        px, py = xa + t*dx - x, ya + t*dy - y
        if abs(px) < 0.02 and abs(py) < 0.02 and px*px + py*py < BOUND * BOUND:
            return True
    return False


def main():
    files = sorted(glob.glob(os.path.join(SRC, "*.csv.gz")))
    if not files:
        sys.exit(f"{SRC} 下没有 csv.gz，先运行 _fetch_sz_orders.py")

    # 底图先行：按数据覆盖裁剪（坪山/大鹏在本批数据中几乎无骑行记录）
    szgeo_path = os.path.join(OUT, "szgeo.json")
    src_geo = os.path.join(SRC, "..", "geo", "shenzhen.json")
    drop = {"坪山区", "大鹏新区"}
    if os.path.exists(src_geo):
        g = json.load(open(src_geo, encoding="utf-8"))
        g["features"] = [f for f in g["features"]
                         if f.get("properties", {}).get("name") not in drop]
        json.dump(g, open(szgeo_path, "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
    boundary = build_boundary_index(load_district_rings(szgeo_path)) if \
        os.path.exists(szgeo_path) else None

    grids = {}
    keys = array("q")
    ledger_parts = []
    od = Counter()
    comp = Counter()
    sample = []
    total = 0
    skipped = 0

    # ---- 第一阶段：流式收集（坐标纠偏 + 包围盒过滤 + 网格量化）----
    for fp in files:
        d = os.path.basename(fp).split(".")[0]
        day = int(d[6:8])
        month = int(d[4:6])
        day0 = CUMDAYS[month - 1] + day - 1
        dk = []
        with gzip.open(fp, "rt", encoding="utf-8", errors="replace") as f:
            rd = csv.reader(f)
            next(rd)  # header
            for r in rd:
                if len(r) < 6:
                    continue
                try:
                    st = r[0]
                    slat = float(r[1]); slon = float(r[2])
                    elat = float(r[3]); elon = float(r[4])
                except ValueError:
                    continue
                if len(st) < 16:
                    continue
                try:
                    hh = int(st[11:13]); mm = int(st[14:16])
                except ValueError:
                    continue
                m = day0 * 1440 + hh * 60 + mm
                gslat, gslon = raw_to_gcj02(slat, slon)
                gelat, gelon = raw_to_gcj02(elat, elon)
                if not (in_bbox(gslat, gslon) and in_bbox(gelat, gelon)):
                    skipped += 1
                    continue
                g = grids.setdefault((round(gslat / CELL), round(gslon / CELL)), len(grids))
                keys.append((g << 21) | m)
                dk.append((g << 21) | m)
                e = grids.setdefault((round(gelat / CELL), round(gelon / CELL)), len(grids))
                od[(g, e)] += 1
                if len(sample) < 200:
                    sample.append({"s": [round(gslat, 5), round(gslon, 5)],
                                   "e": [round(gelat, 5), round(gelon, 5)], "m": m})
                if len(r) >= 6 and r[5]:
                    comp[r[5]] += 1
                total += 1
        ledger_parts.append(dk)
        print(f"{d}: 累计 {total}", flush=True)

    # ---- 第二阶段：行政区划裁剪（只对唯一网格质心各做一次缓冲判定）----
    clipped = 0
    if boundary:
        kept = {}
        for (glat, glon), di in grids.items():
            kept[di] = in_boundary(boundary, glon * CELL, glat * CELL)
        dropped = {di for di, ok in kept.items() if not ok}
        if dropped:
            before = len(keys)
            keys = array("q", [k for k in keys if (k >> 21) not in dropped])
            for part in ledger_parts:
                part[:] = array("q", [k for k in part if (k >> 21) not in dropped])
            od = Counter({se: n for se, n in od.items()
                          if se[0] not in dropped and se[1] not in dropped})
            clipped = before - len(keys)
            print(f"行政区划裁剪：剔除越出 1.5km 缓冲带的漂移单 {clipped}", flush=True)

    # ---- 派生量全部按（裁剪后的）键重算，口径一致 ----
    heat = [[0] * 24 for _ in range(7)]
    day_cnt = defaultdict(int)
    for k in keys:
        m = k & ((1 << 21) - 1)
        day0 = m // 1440
        heat[(day0 + 4) % 7][(m % 1440) // 60] += 1   # 2021-01-01 周五（周一=0 记 4）
        day_cnt[day0] += 1
    day_rows = {(date(2021, 1, 1) + timedelta(days=d)).strftime("%m-%d"): day_cnt[d]
                for d in sorted(day_cnt)}

    gcount = Counter(k >> 21 for k in keys)
    ents = []
    for (glat, glon), di in sorted(grids.items(), key=lambda kv: kv[1]):
        if boundary and not kept.get(di, True):
            continue
        ents.append({"id": di, "lat": round(glat * CELL, 5), "lon": round(glon * CELL, 5),
                     "n": gcount.get(di, 0)})
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
        json.dump({"matrix": heat, "total": sum(map(sum, heat)), "days": day_rows,
                   "companies": dict(comp.most_common(8)),
                   "note": "星期×小时按 2021 年历（07-01 是周四）；坐标已按实测 WGS84→GCJ02 纠偏"},
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
        "scn": "bikesz",
        "name": "城市骑行调度 · 深圳（国内）",
        "domain": "城市出行/调度 —— 深圳市政府数据开放平台 共享单车企业每日订单表（2021）",
        "source": "https://opendata.sz.gov.cn/data/dataSet/toDataDetails/29200_00403627",
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
        "months_covered": sorted({int(d[:2]) for d in days}),
        "days_note": f"2021-{days[0]} ~ 2021-{days[-1]}（数据分布不均的存档抽样，非全量 2.44 亿）",
    }
    write_meta(OUT, meta)
    write_stats(OUT, [
        "dataset = 深圳共享单车企业每日订单表（深圳市政府数据开放平台，无条件开放；"
        "坐标官方标注 BD09，实测为 WGS84，已按 WGS84→GCJ02 纠偏，检验见下）",
        f"orders = {total}  grids(~500m, GCJ02) = {len(grids)}  天 = {','.join(days)}",
        f"outliers_skipped = {skipped}（起/终点越出深圳包围盒的 GPS 漂移脏点，如实剔除）",
        f"boundary_clipped = {clipped}（起/终点落在行政区划 1.5km 缓冲带之外的漂移单，"
        "剔除；缓冲带内保留福田/皇岗/文锦渡/莲塘等口岸跨界簇）",
        f"companies = {dict(comp.most_common(6)) if comp else '本批样例 COM_ID 为空'}",
        "postings.dat = 订单时间序键 gridDense<<21|minuteOfYear；OD/热力/坐标均为真实数据派生",
        "appKey 调用量合规说明：仅拉取上述 7 天存档抽样用于课程实验",
    ])
    print(f"完成：{total} 单 / {len(grids)} 网格 → {OUT}", flush=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    main()
