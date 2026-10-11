#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe reachability / size / Range support of candidate raw sources."""
import ssl
import struct
import urllib.request

UA = {"User-Agent": "CineRank-course/1.0 (educational)"}
CTX = ssl.create_default_context()


def head(url):
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            return r.status, int(r.headers.get("Content-Length") or 0), r.headers.get("Accept-Ranges"), r.headers.get("Content-Type")
    except Exception as e:
        return type(e).__name__ + ": " + str(e)[:120], 0, None, None


def rng(url, a, b):
    h = dict(UA)
    h["Range"] = f"bytes={a}-{b}"
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        return r.status, r.read()


def zip_members(url, size):
    tail = min(size, 1 << 16)
    st, data = rng(url, size - tail, size - 1)
    i = data.rfind(b"PK\x05\x06")
    if i < 0:
        return "no EOCD"
    _, _, _, _, nent, cdsize, cdoff, _ = struct.unpack("<IHHHHIIH", data[i:i + 22])
    st, cd = rng(url, cdoff, cdoff + cdsize - 1)
    out, p = [], 0
    while p + 46 <= len(cd) and cd[p:p + 4] == b"PK\x01\x02":
        (_, _, _, _, method, _, _, _, csz, usz, nlen, elen, clen, _, _, _, loff) = struct.unpack(
            "<IHHHHHHIIIHHHHHII", cd[p:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")
        out.append((name, csz, usz, loff, method))
        p += 46 + nlen + elen + clen
    return out


SOURCES = {
    "amazon_meta": "https://snap.stanford.edu/data/amazon/productGraph/categoryFiles/meta_Electronics.json.gz",
    "web_graph": "https://snap.stanford.edu/data/web-Google.txt.gz",
    "ml25m": "https://files.grouplens.org/datasets/movielens/ml-25m.zip",
    "bike_2019": "https://s3.amazonaws.com/tripdata/2019-citibike-tripdata.zip",
    "bts_2019_01": "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2019_1.zip",
    "openflights_airlines": "https://cdn.jsdelivr.net/gh/jpatokal/openflights@master/data/airlines.dat",
}

for k, u in SOURCES.items():
    print(k, head(u))

for k in ("ml25m", "bike_2019"):
    st, size, _, _ = head(SOURCES[k])
    if isinstance(st, int) and size:
        try:
            ms = zip_members(SOURCES[k], size)
            if isinstance(ms, str):
                print(k, ms)
            else:
                print(k, "members", len(ms))
                for m in ms[:60]:
                    print("   ", m[0], "csz", m[1], "usz", m[2], "off", m[3], "meth", m[4])
        except Exception as e:
            print(k, "zip probe fail", type(e).__name__, e)

try:
    st, data = rng(SOURCES["bts_2019_01"], 0, 3)
    print("bts range", st, data)
except Exception as e:
    print("bts range fail", type(e).__name__, e)
