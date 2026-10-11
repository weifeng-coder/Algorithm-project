#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性增强：把已下载的公开小文件转成 /api/scn/extra 可读取的 JSON。"""
import gzip
import json
import os
import re
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DL = os.path.join(ROOT, "frontend", "_dl")


def rdp(points, eps):
    if len(points) < 3:
        return points
    eps2 = eps * eps

    def rec(pts):
        if len(pts) < 3:
            return pts
        a, b = pts[0], pts[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        den = dx * dx + dy * dy or 1e-18
        maxd, idx = -1, 0
        for i in range(1, len(pts) - 1):
            px, py = pts[i]
            t = ((px - a[0]) * dx + (py - a[1]) * dy) / den
            t = 0 if t < 0 else 1 if t > 1 else t
            qx, qy = a[0] + t * dx, a[1] + t * dy
            d = (px - qx) ** 2 + (py - qy) ** 2
            if d > maxd:
                maxd, idx = d, i
        if maxd > eps2:
            left = rec(pts[: idx + 1])
            right = rec(pts[idx:])
            return left[:-1] + right
        return [pts[0], pts[-1]]

    return rec(points)


def simplify_geom(geom, eps):
    t = geom.get("type")
    if t == "Polygon":
        return {
            "type": "Polygon",
            "coordinates": [
                rdp(ring, eps) if len(ring) > 4 else ring
                for ring in geom["coordinates"]
            ],
        }
    if t == "MultiPolygon":
        return {
            "type": "MultiPolygon",
            "coordinates": [
                [rdp(ring, eps) if len(ring) > 4 else ring for ring in poly]
                for poly in geom["coordinates"]
            ],
        }
    return geom


def parse_csv_line(line):
    parts, cur, q = [], "", False
    for ch in line.rstrip("\n"):
        if ch == '"':
            q = not q
        elif ch == "," and not q:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def norm_name(s):
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    repl = [
        (" street", " st"),
        (" avenue", " ave"),
        (" boulevard", " blvd"),
        (" place", " pl"),
        (" road", " rd"),
        (" east ", " e "),
        (" west ", " w "),
        (" north ", " n "),
        (" south ", " s "),
    ]
    for a, b in repl:
        s = s.replace(a, b)
    return s


def tokens(s):
    return tuple(sorted(norm_name(s).split()))


def main():
    iatas = json.load(open(os.path.join(DATA, "flight", "_iata.json"), encoding="utf-8"))

    of = {}
    with open(os.path.join(DL, "airports.bin"), encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = parse_csv_line(line)
            if len(parts) < 8:
                continue
            iata = parts[4].strip()
            if not iata or iata == "\\N":
                continue
            try:
                lat = float(parts[6])
                lon = float(parts[7])
            except ValueError:
                continue
            of[iata] = {
                "name": parts[1],
                "city": parts[2],
                "country": parts[3],
                "lat": lat,
                "lon": lon,
            }

    geo, miss = {}, []
    for dens, code in iatas:
        if code in of:
            d = of[code]
            geo[code] = {
                "id": dens,
                "lat": round(d["lat"], 5),
                "lon": round(d["lon"], 5),
                "name": d["name"],
                "city": d["city"],
                "country": d["country"],
            }
        else:
            miss.append(code)
    print("flight geo", len(geo), "/", len(iatas), "miss", miss)

    with open(os.path.join(DATA, "flight", "flightgeo.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "airports": geo,
                "meta": {
                    "cover": len(geo),
                    "total": len(iatas),
                    "miss": miss,
                    "source": "https://github.com/jpatokal/openflights",
                    "downloaded": "2026-09-29",
                },
            },
            f,
            ensure_ascii=False,
        )

    delay = {
        "bins": [
            {"i": 0, "label": "提前/准点", "hi": 0, "n": 1490833},
            {"i": 1, "label": "<=5 min", "lo": 0, "hi": 5, "n": 328646},
            {"i": 2, "label": "<=15 min", "lo": 5, "hi": 15, "n": 199428},
            {"i": 3, "label": "<=30 min", "lo": 15, "hi": 30, "n": 142391},
            {"i": 4, "label": "<=60 min", "lo": 30, "hi": 60, "n": 120782},
            {"i": 5, "label": "<=120 min", "lo": 60, "hi": 120, "n": 82276},
            {"i": 6, "label": "<=180 min", "lo": 120, "hi": 180, "n": 27723},
            {"i": 7, "label": "<=300 min", "lo": 180, "hi": 300, "n": 16909},
            {"i": 8, "label": "<=600 min", "lo": 300, "hi": 600, "n": 5780},
            {"i": 9, "label": ">600 min", "lo": 600, "n": 2254},
        ],
        "ontime_def": "delayIdx <= 2 (<=15 min)",
        "ontime_n": 1490833 + 328646 + 199428,
        "flights": 2417022,
        "months": [1, 10, 11, 12],
        "months_note": "仅 1 / 10 / 11 / 12 月（2-9 月直链不可达，未插值）",
        "airports": 356,
        "carriers": 17,
        "mean_note": "当前 mean 是延误档位号 0-9，不是分钟",
        "geo_cover": [len(geo), len(iatas)],
        "geo_miss": miss,
        "keyPack": "origin<<25 | minuteOfYear<<4 | delayIdx",
    }
    with open(os.path.join(DATA, "flight", "delayhist.json"), "w", encoding="utf-8") as f:
        json.dump(delay, f, ensure_ascii=False)

    us = json.load(open(os.path.join(DL, "usstates.bin"), encoding="utf-8"))
    print("us-states feats", len(us.get("features", [])))
    us2 = {"type": "FeatureCollection", "features": []}
    for feat in us["features"]:
        us2["features"].append(
            {
                "type": "Feature",
                "properties": feat.get("properties", {}),
                "geometry": simplify_geom(feat["geometry"], 0.02),
            }
        )
    outp = os.path.join(DATA, "flight", "usstates.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(us2, f, separators=(",", ":"))
    print("usstates", os.path.getsize(os.path.join(DL, "usstates.bin")), "->", os.path.getsize(outp))

    nyc = json.load(open(os.path.join(DL, "nycgeo.bin"), encoding="utf-8"))
    print("nyc feats", len(nyc.get("features", [])), "props", list(nyc["features"][0]["properties"].keys())[:10])
    nyc2 = {"type": "FeatureCollection", "features": []}
    for feat in nyc["features"]:
        props = feat.get("properties", {})
        keep = {}
        for k, v in props.items():
            if k.lower() in ("name", "borough", "boroname", "bname") and isinstance(v, str):
                keep[k] = v
        if not keep:
            for k, v in props.items():
                if isinstance(v, str) and 1 < len(v) < 40:
                    keep = {"name": v}
                    break
        nyc2["features"].append(
            {
                "type": "Feature",
                "properties": keep or {"name": "NYC"},
                "geometry": simplify_geom(feat["geometry"], 0.0006),
            }
        )
    outp = os.path.join(DATA, "bike", "nycgeo.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(nyc2, f, separators=(",", ":"))
    print("nycgeo", os.path.getsize(os.path.join(DL, "nycgeo.bin")), "->", os.path.getsize(outp))

    st_raw = json.load(open(os.path.join(DL, "citibike.bin"), encoding="utf-8"))
    gbfs_list = st_raw.get("data", {}).get("stations") or st_raw.get("stations") or []
    print("gbfs stations", len(gbfs_list))

    by_id, by_name, by_tok = {}, {}, {}
    for s in gbfs_list:
        name = s.get("name") or ""
        lat, lon = s.get("lat"), s.get("lon")
        if lat is None or lon is None:
            continue
        rec = {
            "lat": float(lat),
            "lon": float(lon),
            "name": name,
            "gbfs_id": str(s.get("station_id") or ""),
            "capacity": s.get("capacity"),
        }
        sid = str(s.get("station_id") or "")
        short = str(s.get("short_name") or "")
        if sid:
            by_id[sid] = rec
        if short:
            by_id[short] = rec
        if name:
            by_name[norm_name(name)] = rec
            by_tok[tokens(name)] = rec

    ours = json.load(open(os.path.join(DATA, "bike", "_stations.json"), encoding="utf-8"))
    how = defaultdict(int)
    outst = {}
    for sid, nm, _raw in ours:
        rec = None
        src = None
        if str(sid) in by_id:
            rec, src = by_id[str(sid)], "id"
        elif norm_name(nm) in by_name:
            rec, src = by_name[norm_name(nm)], "name"
        elif tokens(nm) in by_tok:
            rec, src = by_tok[tokens(nm)], "tok"
        if rec:
            how[src] += 1
            outst[str(sid)] = {
                "id": sid,
                "lat": round(rec["lat"], 5),
                "lon": round(rec["lon"], 5),
                "name": nm,
                "src": src,
            }
        else:
            outst[str(sid)] = {"id": sid, "lat": None, "lon": None, "name": nm, "src": None}

    cover = sum(1 for v in outst.values() if v["lat"] is not None)
    print("bike match", cover, "/", len(ours), dict(how))
    um = [(v["id"], v["name"]) for v in outst.values() if v["lat"] is None]
    print("unmatched sample", um[:12])

    meta = {
        "cover": cover,
        "total": len(ours),
        "source": "Citi Bike GBFS station_information.json (current, not 2019 snapshot)",
        "downloaded": "2026-09-29",
        "note": "2019 站点 id/名与现行 GBFS 不完全一致；无坐标的站点不落点",
        "months_note": "骑行数据仅 1-9 月",
        "how": dict(how),
    }
    with open(os.path.join(DATA, "bike", "bikestn.json"), "w", encoding="utf-8") as f:
        json.dump({"stations": outst, "meta": meta}, f, ensure_ascii=False)

    genes = []
    with gzip.open(os.path.join(DL, "gff.bin"), "rt", encoding="utf-8") as f:
        for line in f:
            if not line or line[0] == "#":
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 9 or p[2] != "gene":
                continue
            start, end, strand = int(p[3]), int(p[4]), p[6]
            attrs = {}
            for kv in p[8].split(";"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    attrs[k] = v
            name = attrs.get("Name") or attrs.get("gene") or attrs.get("ID", "")
            product = attrs.get("product") or attrs.get("description") or ""
            genes.append(
                {"name": name, "start": start, "end": end, "strand": strand, "product": product}
            )
    print("genes", len(genes), "first", genes[:2])
    with open(os.path.join(DATA, "gene", "genegenes.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "n": len(genes),
                "source": "NCBI GCF_000005845.2 GFF",
                "downloaded": "2026-09-29",
                "genes": genes,
            },
            f,
            ensure_ascii=False,
        )

    print("sizes:")
    for p in [
        "flight/flightgeo.json",
        "flight/delayhist.json",
        "flight/usstates.json",
        "bike/bikestn.json",
        "bike/nycgeo.json",
        "movie/movieheat.json",
        "gene/genegenes.json",
    ]:
        fp = os.path.join(DATA, p)
        print(" ", p, os.path.getsize(fp) if os.path.exists(fp) else "MISSING")


if __name__ == "__main__":
    main()
