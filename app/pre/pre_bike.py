#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_bike.py —— 场景 C：Citi Bike 2019 骑行日志 → 城市调度场景数据

数据来源：https://s3.amazonaws.com/tripdata/2019-citibike-tripdata.zip（858MB，
2019 全年 42 个月度分片 CSV，约 1,700 万次骑行；zip 内直接流式读取，不解压落盘）。

键打包（多场景应用方案 C 节）：
  postings.dat  key = stationId << 21 | minuteOfYear（站点 <2^11，年内分钟 <2^21）
                —— 按 zip 内自然顺序（时间序）写出 = 真实日志序；
  ledger.dat    台账序 = 逐月分片内部按站点聚合、站内按时间升序的拼接序列
                —— 每月台账有序、月份之间拼接 = **真实版"块状近序"**，
                是 AdaptSort run-merge 路径（CVE替身分布）的现实原型。

排序任务：热门站点榜（count↓，k≈1,000 计数排序优势场景）、稳定性现实意义
（台账重排时稳定排序自动保持站内时间序）。

运行：python app/pre/pre_bike.py <2019-citibike-tripdata.zip 路径>
"""

import csv
import io
import json
import os
import sys
import zipfile
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (JsonlGzWriter, outdir_of, human, write_entities, write_meta,
                    write_sample, write_stats, write_tiers)

SRC_URL = "https://s3.amazonaws.com/tripdata/2019-citibike-tripdata.zip"
CUMDAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]  # 2019 非闰年


def minute_of_year(ts):
    """'2019-07-04 08:30:15' → 年内分钟（≤ 525599 < 2^21）"""
    try:
        month = int(ts[5:7]); day = int(ts[8:10])
        hh = int(ts[11:13]); mm = int(ts[14:16])
        return (CUMDAYS[month - 1] + day - 1) * 1440 + hh * 60 + mm
    except (ValueError, IndexError):
        return -1


def main(srczip):
    outdir = outdir_of("bike")
    os.makedirs(outdir, exist_ok=True)
    keys = array("q")               # postings：时间序（真实日志序）
    ledgerParts = []                # 逐月台账键（块内有序，块间拼接）
    stations = {}                   # 站点 id → name（首个出现者）
    total = 0
    jw = JsonlGzWriter(os.path.join(outdir, "records.jsonl.gz"))
    print(f"[bike] 流式解析 {srczip} ...")

    with zipfile.ZipFile(srczip) as zf:
        members = sorted(n for n in zf.namelist() if n.endswith(".csv"))
        print(f"[bike] zip 内 {len(members)} 个分片")
        for mi, name in enumerate(members):
            monthKeys = []
            with zf.open(name) as raw:
                # 站点名/车型字段可能含引号内逗号 —— 用 csv 模块防列错位
                text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                reader = csv.reader(text)
                header = [h.strip('"') for h in next(reader)]
                try:
                    iT0 = header.index("tripduration")
                    iSt = header.index("starttime")
                    iSs = header.index("start station id")
                    iSn = header.index("start station name")
                    iEs = header.index("end station id")
                    iEn = header.index("end station name")
                    iBk = header.index("bikeid")
                except ValueError:
                    print(f"  !! {name} 表头不符合 2019 schema，跳过")
                    continue
                for p in reader:
                    if len(p) <= iBk:
                        continue
                    try:
                        sid = int(p[iSs]); eid = int(p[iEs])
                        dur = int(p[iT0]); bike = int(p[iBk])
                    except ValueError:
                        continue
                    m = minute_of_year(p[iSt])
                    if m < 0:
                        continue
                    if sid not in stations:
                        stations[sid] = p[iSn]
                    if eid not in stations:
                        stations[eid] = p[iEn]
                    keys.append((sid << 21) | m)
                    monthKeys.append((sid << 21) | m)
                    jw.write({"s": sid, "e": eid, "m": m, "d": dur, "b": bike})
                    total += 1
            # 台账块：月内按站点聚合（station 主键升序，站内保持时间序）
            monthKeys.sort()
            ledgerParts.append(monthKeys)
            print(f"  [{mi+1}/{len(members)}] {os.path.basename(name)}: "
                  f"累计 {human(total)} 骑行")
    jw.close()
    nStations = len(stations)
    print(f"[bike] 骑行 {total}，站点 {nStations}")

    # 台账序落盘（块状近序：月内站点有序）
    ledger = array("q")
    for part in ledgerParts:
        ledger.extend(part)
    from common import write_keys
    write_keys(os.path.join(outdir, "ledger.dat"), ledger)
    del ledgerParts, ledger

    tiers = write_tiers(outdir, keys)
    del keys

    ents = [{"id": sid, "year": 0, "category": "(station)", "val": 0,
             "name": f"{sid} {nm}"} for sid, nm in sorted(stations.items())]
    write_entities(os.path.join(outdir, "entities.dat"), ents)

    sample = []
    with zipfile.ZipFile(srczip) as zf:
        with zf.open(members[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            reader = csv.reader(text)
            hdr = [h.strip('"') for h in next(reader)]
            idx = {c: i for i, c in enumerate(hdr)}
            for p in reader:
                if len(p) <= idx["bikeid"]:
                    continue
                sample.append({"duration": p[idx["tripduration"]].strip('"'),
                               "start": p[idx["starttime"]].strip('"'),
                               "from": f'{p[idx["start station id"]].strip(chr(34))} {p[idx["start station name"]].strip(chr(34))}',
                               "bike": p[idx["bikeid"]].strip('"')})
                if len(sample) >= 200:
                    break
    write_sample(os.path.join(outdir, "records_sample.json"), sample)

    meta = {
        "scn": "bike",
        "name": "城市骑行调度",
        "domain": "城市出行/物流调度 —— 排序作为调度系统的关键子问题",
        "source": SRC_URL,
        "downloaded": "2026-09-28",
        "keyPack": "key = stationId<<21 | minuteOfYear（32 位）",
        "attrBits": 0, "subBits": 21,
        "entityLabel": "站点", "subLabel": "年内心分钟", "attrLabel": "",
        "countLabel": "骑行量", "valLabel": "—",
        "rankModes": {"pop": "热门站点（骑行量↓）"},
        "extra": ["ledger"],
    }
    write_meta(outdir, meta)

    write_stats(outdir, [
        f"dataset = Citi Bike 2019 年 1-9 月（S3 年度 zip 实际包含的分片）（{SRC_URL}）",
        f"trips = {total}  stations = {nStations}",
        f"postings.dat = {tiers['full'][0]} keys / {human(tiers['full'][1])}B（zip 自然序 = 时间序）",
        f"ledger.dat = 台账序（逐月分片内站点升序拼接）——真实版块状近序",
        "键打包: stationId<<21 | minuteOfYear；站点数小（k≈1e3）→ 计数排序优势场景",
    ])
    print(f"[bike] 完成: {total} trips, {nStations} stations")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "../downloads/bike/2019-citibike-tripdata.zip"
    main(src)
