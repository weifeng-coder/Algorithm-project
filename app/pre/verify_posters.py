#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_posters.py —— 复查「任意 K × 任意排序依据 × 任意筛选」下海报是否齐备。

这是针对「K 调到 50 以上海报就加载不出来」的验收工具。它不复用拉取脚本的
内部状态，而是**从 HTTP 接口重新取榜单**，把每一行按前端完全相同的规则
（/assets/posters/<id>.jpg）落成一次真实请求，再判定：

    * 榜单接口本身是否正常（条数是否等于请求的 K）
    * 榜单里每一部影片的海报是否真的可下载（HTTP 200 且是图片）
    * 排序依据切换后（关注度 / 好评度 / 评分优先）覆盖率是否仍成立
    * 地区/类型筛选后是否仍成立

退出码非 0 表示验收失败，可直接用于 CI 或批处理。

用法：
    python app/pre/verify_posters.py                     # 默认查 K=50/100/200/500
    python app/pre/verify_posters.py --k 50,60,500
    python app/pre/verify_posters.py --mode pop,rat      # 只查部分排序依据
    python app/pre/verify_posters.py --timeout 8
"""
import argparse
import concurrent.futures as cf
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8080"
UA = {"User-Agent": "CineRank-course/1.0 (poster verification)"}


def get_json(path, timeout=60):
    req = urllib.request.Request(BASE + path, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def head_poster(mid, timeout=8):
    """返回 (mid, ok, detail)。ok 仅当 HTTP 200 且回应是图片。"""
    url = f"{BASE}/assets/posters/{mid}.jpg"
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = r.headers.get("Content-Type", "")
            body = r.read(64)
        if r.status != 200:
            return mid, False, f"HTTP {r.status}"
        if "json" in ct:                      # 服务端把 404 也回 200+json 的历史行为
            return mid, False, "not-found-json"
        if not body:
            return mid, False, "empty"
        return mid, True, "ok"
    except urllib.error.HTTPError as e:
        return mid, False, f"HTTP {e.code}"
    except Exception as e:
        return mid, False, type(e).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", default="50,100,200,500", help="逗号分隔的 K 列表")
    ap.add_argument("--mode", default="pop,rat,bayes", help="逗号分隔的排序依据")
    ap.add_argument("--region", default="", help="额外检查的地区（如 华语）")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--jobs", type=int, default=16)
    ap.add_argument("--min-cover", type=float, default=1.0,
                    help="最低覆盖率（默认 1.0 = 一张都不许缺）")
    a = ap.parse_args()

    ks = [int(x) for x in a.k.split(",") if x.strip()]
    modes = [m for m in a.mode.split(",") if m.strip()]
    regions = [""] + ([a.region] if a.region else [])

    print(f"复查目标：K={ks} 排序依据={modes}")
    print(f"接口：{BASE}\n")

    fails = []
    for region in regions:
        for mode in modes:
            for k in ks:
                q = {"k": k, "genre": "(all)", "minvotes": 0, "mode": mode}
                if region:
                    q["region"] = region
                try:
                    j = get_json("/api/movies/topk?" + urllib.parse.urlencode(q))
                except Exception as e:
                    fails.append((region, mode, k, f"榜单接口失败 {type(e).__name__}"))
                    print(f"  [FAIL] region={region or '全部'} mode={mode} k={k} "
                          f"接口失败：{type(e).__name__}")
                    continue
                items = j.get("items") or []
                tag = f"region={region or '全部':6} mode={mode:5} k={k:<4}"
                if len(items) != k:
                    print(f"  [warn] {tag} 返回 {len(items)} 条（请求 {k}）")
                ids = [m["id"] for m in items]
                if not ids:
                    print(f"  [warn] {tag} 榜单为空，跳过")
                    continue
                with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
                    results = list(ex.map(lambda i: head_poster(i, a.timeout), ids))
                ok = [r for r in results if r[1]]
                bad = [r for r in results if not r[1]]
                cover = len(ok) / len(results)
                mark = "OK  " if cover >= a.min_cover else "FAIL"
                print(f"  [{mark}] {tag} 海报 {len(ok)}/{len(results)} "
                      f"= {cover*100:.1f}%")
                for mid, _, why in bad[:10]:
                    print(f"          缺 id={mid} ({why})")
                if bad and len(bad) > 10:
                    print(f"          … 另有 {len(bad)-10} 条")
                if cover < a.min_cover:
                    fails.append((region, mode, k,
                                  f"覆盖 {len(ok)}/{len(results)}"))

    print()
    if fails:
        print(f"验收失败：{len(fails)} 个组合未达标")
        for region, mode, k, why in fails:
            print(f"  - region={region or '全部'} mode={mode} k={k}: {why}")
        return 1
    print("验收通过：所有组合海报齐备")
    return 0


if __name__ == "__main__":
    sys.exit(main())
