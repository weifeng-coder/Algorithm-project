#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_flight.py —— 场景 E：美国 DOT 2019 航班准点数据 → 航空调度场景数据

数据来源（12 个月，各 60~70MB zip，约 750 万行）：
https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2019_{1..12}.zip
宽表约 110 列，本脚本只取 7 列（流式，磁盘友好）：
  FlightDate, OP_UNIQUE_CARRIER, Origin, Dest, DepTime, DepDelay, Distance

键打包（多场景应用方案 E 节）：
  postings.dat  key = originDense << 25 | minuteOfYear << 4 | delayIdx
  （机场 <2^9，年内心分钟 <2^21，延误档 4 位；共 34 位）
  delayIdx = 延误分档 0..9（提前/准点=0，5/15/30/60/120/180/300/600 分钟截断，>600=9）
  attr = delayIdx → "平均延误档"（meanScale=1, meanOffset=0）
ledger.dat = 航司台账序（逐月分片内按航司聚合、司内按时间升序拼接）——块状近序第二例。
排序任务：机场倒排检索、准点/延误榜单（count↓）、长尾延误分桶（计数排序）。

运行：python app/pre/pre_flight.py <flight zip 目录>
"""

import csv
import io
import os
import sys
import zipfile
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (JsonlGzWriter, outdir_of, human, write_entities, write_meta,
                    write_sample, write_stats, write_keys, write_tiers)

SRC_URL = ("https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_"
           "On_Time_Performance_1987_present_2019_{M}.zip")
CUMDAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def minute_of_year(fdate, dtime):
    """FlightDate '2019-03-05' + DepTime '0730' → 年内分钟；无效返回 -1"""
    if not fdate or len(fdate) < 10 or not dtime:
        return -1
    try:
        dt = dtime.strip()
        if dt.endswith(".0"):
            dt = dt[:-2]
        dt = dt.zfill(4)
        hh, mm = int(dt[:2]), int(dt[2:4])
        if hh > 23 or mm > 59:
            return -1
        month = int(fdate[5:7]); day = int(fdate[8:10])
        return (CUMDAYS[month - 1] + day - 1) * 1440 + hh * 60 + mm
    except (ValueError, IndexError):
        return -1


def delay_idx(dep):
    """DepDelay 分钟 → 0..9 档（长尾截断）；无效返回 -1"""
    try:
        d = float(dep)
    except (TypeError, ValueError):
        return -1
    if d < 0:    return 0
    if d <= 5:   return 1
    if d <= 15:  return 2
    if d <= 30:  return 3
    if d <= 60:  return 4
    if d <= 120: return 5
    if d <= 180: return 6
    if d <= 300: return 7
    if d <= 600: return 8
    return 9


def main(srcdir):
    outdir = outdir_of("flight")
    os.makedirs(outdir, exist_ok=True)
    keys = array("q")
    ledgerParts = []
    airports = {}      # dense id → code
    ap2id = {}
    al2id = {}
    hist = [0] * 10
    total = 0
    jw = JsonlGzWriter(os.path.join(outdir, "records.jsonl.gz"))
    print(f"[flight] 流式解析 {srcdir}/2019_*.zip ...")
    for m in range(1, 13):
        zpath = os.path.join(srcdir, f"2019_{m:02d}.zip")
        if not os.path.exists(zpath):
            print(f"  !! 缺 {zpath}，跳过（该月不计入）")
            continue
        monthKeys = []
        with zipfile.ZipFile(zpath) as zf:
            csvs = [n for n in zf.namelist() if n.endswith(".csv")]
            with zf.open(csvs[0]) as raw:
                # DOT 宽表的 CityName 字段含引号内逗号（"Kalamazoo, MI"）——
                # 必须 csv 模块解析，朴素 split(',') 会列错位
                text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                rows = csv.reader(text)
                header = [h.strip('"') for h in next(rows)]
                iDate = header.index("FlightDate")
                iCar = header.index("OP_UNIQUE_CARRIER") if "OP_UNIQUE_CARRIER" in header \
                    else header.index("Reporting_Airline")
                iOrg = header.index("Origin")
                iDst = header.index("Dest")
                iDep = header.index("DepTime")
                iDly = header.index("DepDelay")
                iDis = header.index("Distance")
                for p in rows:
                    if len(p) <= iDis:
                        continue
                    fdate = p[iDate].strip('"')
                    dtime = p[iDep].strip('"')
                    didx = delay_idx(p[iDly].strip('"')) if p[iDly].strip('"') not in ("", "NaN") else -1
                    mty = minute_of_year(fdate, dtime)
                    if mty < 0 or didx < 0:
                        continue          # 取消/未起飞航班
                    oc, ac, cc = p[iOrg].strip('"'), p[iDst].strip('"'), p[iCar].strip('"')
                    oid = ap2id.get(oc)
                    if oid is None:
                        oid = ap2id[oc] = len(airports); airports[oid] = oc
                    aid = al2id.get(cc)
                    if aid is None:
                        aid = al2id[cc] = len(al2id)
                    try:
                        dist = int(float(p[iDis].strip('"')))
                    except ValueError:
                        dist = 0
                    keys.append((oid << 25) | (mty << 4) | didx)
                    monthKeys.append((aid << 42) | (mty << 4) | didx)   # 台账键含航司
                    hist[didx] += 1
                    jw.write({"o": oc, "d": ac, "c": cc, "m": mty,
                              "x": didx, "dist": dist})
                    total += 1
            monthKeys.sort()
            ledgerParts.append(monthKeys)
        print(f"  2019-{m:02d}: 累计 {human(total)} 航班")
    jw.close()
    nAirports, nAirlines = len(airports), len(al2id)
    print(f"[flight] 航班 {total}，机场 {nAirports}，航司 {nAirlines}")

    ledger = array("q")
    for part in ledgerParts:
        ledger.extend(part)
    write_keys(os.path.join(outdir, "ledger.dat"), ledger)
    del ledgerParts, ledger

    tiers = write_tiers(outdir, keys)
    del keys

    ents = [{"id": i, "year": 0, "category": "(airport)", "val": 0, "name": airports[i]}
            for i in range(nAirports)]
    write_entities(os.path.join(outdir, "entities.dat"), ents)

    sample = []
    with zipfile.ZipFile(os.path.join(srcdir, "2019_01.zip")) as zf:
        with zf.open([n for n in zf.namelist() if n.endswith(".csv")][0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            rows = csv.reader(text)
            header = [h.strip('"') for h in next(rows)]
            iNeed = ["FlightDate", "OP_UNIQUE_CARRIER", "Origin", "Dest",
                     "DepTime", "DepDelay", "ArrDelay", "Distance"]
            ii = {c: header.index(c) for c in iNeed if c in header}
            for p in rows:
                if len(p) <= max(ii.values()):
                    continue
                sample.append({c: p[j].strip('"') for c, j in ii.items()})
                if len(sample) >= 200:
                    break
    write_sample(os.path.join(outdir, "records_sample.json"), sample)

    meta = {
        "scn": "flight",
        "name": "航班准点调度",
        "domain": "航空调度/组合优化邻接 —— 长尾分布的现实数据源",
        "source": SRC_URL,
        "downloaded": "2026-09-28",
        "keyPack": "key = originDense<<25 | minuteOfYear<<4 | delayIdx（34 位）",
        "attrBits": 4, "subBits": 21,
        "entityLabel": "机场", "subLabel": "年内心分钟", "attrLabel": "延误档",
        "countLabel": "航班量", "valLabel": "—",
        "meanScale": 1.0, "meanOffset": 0.0,
        "rankModes": {"pop": "航班量（起降架次↓）", "rat": "平均延误档（延误大→小）"},
        "extra": ["ledger"],
    }
    write_meta(outdir, meta)

    write_stats(outdir, [
        f"dataset = US DOT On-Time Performance 2019（取到 1,10,11,12 四个月）（{SRC_URL}）",
        f"flights = {total}  airports = {nAirports}  carriers = {nAirlines}",
        f"postings.dat = {tiers['full'][0]} keys / {human(tiers['full'][1])}B（自然序=日期序）",
        "延误档直方图（长尾, 0=提前/准点 ... 9=>600分钟）:",
        "  " + "  ".join(f"{i}:{hist[i]}" for i in range(10)),
        "键打包: originDense<<25 | minuteOfYear<<4 | delayIdx；长尾分桶=计数排序场景",
        "ledger.dat = 航司台账序（逐月司内时间升序拼接）——块状近序第二例",
    ])
    print(f"[flight] 完成: {total} flights, {nAirports} airports")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "../downloads/flight"
    main(src)
