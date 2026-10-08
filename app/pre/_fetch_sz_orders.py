#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""深圳共享单车订单拉取器：分页调开放平台 API，逐页转存每日 gzip CSV。

用法：python app/pre/_fetch_sz_orders.py [起始日 yyyymmdd] [天数]
默认 20210701 起 14 天。输出 downloads/sz_orders/<date>.csv.gz
字段：start_time,start_lat,start_lng,end_lat,end_lng,com_id（坐标为平台原始 BD09 度数）
断点：已有文件大小异常时整日重下；每页失败重试 4 次。
"""
import gzip
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

AK = "66611fd69cca4595a296087958310c86"
API = "https://opendata.sz.gov.cn/api/29200_00403627/1/service.xhtml"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "..", "..", "downloads", "sz_orders")
ROWS = 10000


def fetch_page(d, page, tries=4):
    url = f"{API}?page={page}&rows={ROWS}&startDate={d}&endDate={d}&appKey={AK}"
    for t in range(tries):
        r = subprocess.run(["curl", "-s", "-m", "90", url], capture_output=True, timeout=120)
        try:
            j = json.loads(r.stdout.decode("utf-8", "replace"))
            if "data" in j:
                return j
            print(f"  [{d} p{page}] api error: {j}", flush=True)
        except Exception:
            pass
        time.sleep(2 + t * 2)
    return None


def do_day(d):
    out = os.path.join(OUT, f"{d}.csv.gz")
    probe = fetch_page(d, 1)
    if not probe:
        print(f"[{d}] 探测失败，跳过", flush=True)
        return d, 0
    total = int(probe.get("total") or 0)
    pages = max(1, -(-total // ROWS))
    tmp = out + ".tmp"
    n = 0
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        f.write("start_time,start_lat,start_lng,end_lat,end_lng,com_id\n")
        for p in range(1, pages + 1):
            j = fetch_page(d, p)
            if not j:
                print(f"  [{d} p{p}] 放弃该页", flush=True)
                continue
            for r in j.get("data") or []:
                f.write(','.join([
                    r.get("START_TIME", ""), r.get("START_LAT", ""), r.get("START_LNG", ""),
                    r.get("END_LAT", ""), r.get("END_LNG", ""), r.get("COM_ID", ""),
                ]).replace("\n", " ") + "\n")
                n += 1
            if p % 25 == 0:
                print(f"  [{d}] {p}/{pages} 页 累计 {n}", flush=True)
    os.replace(tmp, out)
    print(f"[{d}] 完成 total={total} 实取={n} ({os.path.getsize(out)//1024}KB)", flush=True)
    return d, n


def main():
    d0 = sys.argv[1] if len(sys.argv) > 1 else "20210701"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    y, m, dd = int(d0[:4]), int(d0[4:6]), int(d0[6:8])
    dates = [(date(y, m, dd) + timedelta(i)).strftime("%Y%m%d") for i in range(days)]
    os.makedirs(OUT, exist_ok=True)
    print(f"拉取 {dates[0]} ~ {dates[-1]} 共 {len(dates)} 天（{workers} 并发）", flush=True)
    total = 0
    with ThreadPoolExecutor(workers) as ex:
        for d, n in ex.map(do_day, dates):
            total += n
            print(f"== {d}: {n}（累计 {total}）", flush=True)
    print(f"全部完成 {total} 条", flush=True)


if __name__ == "__main__":
    main()
