#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_geocode_bike_grids.py —— 给骑行网格补真实行政区名（深圳 / 北京）

自由流单车没有固定站点，实体是 ~500m 起点网格。网格编号对读者没有可读性，
所以这里用行政区划 GeoJSON 做「网格中心点 → 所在区」的判定，产出真实地名：

    gridgeo.json 每个网格增加 dist（区名，如 福田区）与 area（区名 + 方位描述）

判定用射线法（point-in-polygon），对 MultiPolygon 逐个环测试；落在所有区之外
的网格如实标为 "(区界外)"，不猜、不就近硬凑。深圳底图只含 8 个区（坪山/大鹏
在数据中几乎无记录已裁剪），北京 16 个区。

产物：app/data/<scn>/gridgeo.json 增加 dist / area 字段（原地更新，先备份）。
运行：python app/pre/_geocode_bike_grids.py
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # app/
PROJ = os.path.dirname(ROOT)                                          # 工程根
GEO = os.path.join(PROJ, "downloads", "geo")

TARGETS = [
    ("bikesz", "shenzhen.json"),
    ("bikecn", "beijing.json"),
]


def point_in_ring(x, y, ring):
    """射线法：点 (x,y) 是否在 ring 内。ring = [[lon,lat], ...]"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def build_polys(geojson):
    """→ [(区名, [外环, 内环...]), ...]"""
    out = []
    for f in geojson.get("features", []):
        name = (f.get("properties") or {}).get("name") or "?"
        g = f.get("geometry") or {}
        gt = g.get("type")
        coords = g.get("coordinates") or []
        polys = [coords] if gt == "Polygon" else coords
        for poly in polys:
            if poly:
                out.append((name, poly))     # poly[0]=外环, poly[1:]=洞
    return out


def locate(x, y, polys):
    """点定位到区：外环命中且不在任何洞里 → 区名，否则 None"""
    for name, poly in polys:
        if not point_in_ring(x, y, poly[0]):
            continue
        in_hole = any(point_in_ring(x, y, ring) for ring in poly[1:])
        if not in_hole:
            return name
    return None


def compass(cx, cy, lat, lon):
    """相对城市中心的方位，把"福田区"细化为"福田区西南"这类可读位置。
    中文习惯是东/西在前、南/北在后（西南、东北），故按 西/东 + 南/北 拼接。"""
    ew = "东" if lon > cx else ("西" if lon < cx else "")
    ns = "北" if lat > cy else ("南" if lat < cy else "")
    return (ew + ns) or "中部"


def main():
    for scn, geofile in TARGETS:
        gp = os.path.join(GEO, geofile)
        gg = os.path.join(ROOT, "data", scn, "gridgeo.json")
        if not os.path.exists(gp):
            print(f"[{scn}] 缺少 {gp}，跳过")
            continue
        if not os.path.exists(gg):
            print(f"[{scn}] 缺少 {gg}，跳过")
            continue
        polys = build_polys(json.load(open(gp, encoding="utf-8")))
        grids = json.load(open(gg, encoding="utf-8"))
        g = grids.get("grids") or {}

        lats = [v["lat"] for v in g.values()]
        lons = [v["lon"] for v in g.values()]
        cy = sum(lats) / len(lats)
        cx = sum(lons) / len(lons)

        hit = miss = 0
        for gid, v in g.items():
            lat, lon = v["lat"], v["lon"]
            name = locate(lon, lat, polys)
            if name:
                v["dist"] = name
                v["area"] = f"{name}{compass(cx, cy, lat, lon)}"
                hit += 1
            else:
                v["dist"] = ""
                v["area"] = "(区界外)"
                miss += 1

        # 备份原文件（只备份一次）
        bak = gg + ".noname.bak"
        if not os.path.exists(bak):
            shutil.copyfile(gg, bak)

        grids["geoNote"] = ("网格已用行政区划 GeoJSON 做点定位，dist=所在区，"
                            "area=区+相对城市中心的方位；落在区界外的如实标为「(区界外)」")
        with open(gg, "w", encoding="utf-8") as f:
            json.dump(grids, f, ensure_ascii=False, separators=(",", ":"))

        # 命中区的网格数 / 总网格数
        print(f"[{scn}] 网格 {len(g)} 个：定位到区 {hit}，区界外 {miss}"
              f"（{hit / max(1, len(g)) * 100:.1f}%）")
        top = sorted(g.items(), key=lambda kv: -kv[1]["n"])[:8]
        for gid, v in top:
            print(f"    网格{gid} → {v['area']}  n={v['n']}")


if __name__ == "__main__":
    main()
