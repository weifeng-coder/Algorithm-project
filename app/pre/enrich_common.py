#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""enrich_common.py —— 增强管线共用工具（前端重构方案 §4）

原始数据下载到工程根上一级的 downloads/<场景>/，与 download_data.py 同口径；
make_package.py 只打包工程根，原始数据不会随包转发。产物写到 app/data/<场景>/，
文件名只用小写字母/数字/下划线/点，供 /api/scn/extra 读取。
单个数据类型的下载量上限 300MB，超过则拒绝下载。
"""
import json
import os
import ssl
import struct
import time
import urllib.error
import urllib.request
import zlib

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(APP)
DL = os.path.join(os.path.dirname(ROOT), "downloads")
DATA = os.path.join(APP, "data")
ASSETS = os.path.join(APP, "frontend", "assets")
LIMIT = 300 * 1024 * 1024
UA = {"User-Agent": "CineRank-course/1.0 (educational data enrichment)"}
CTX = ssl.create_default_context()


def log(*a):
    print(*a, flush=True)


def dl_path(scn, name):
    d = os.path.join(DL, scn)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def write_json(scn, name, obj):
    d = os.path.join(DATA, scn)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)
    log(f"  -> app/data/{scn}/{name}  {os.path.getsize(p):,} B")


def urlopen(url, headers=None, timeout=90, data=None):
    h = dict(UA)
    h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, headers=h, data=data),
                                  timeout=timeout, context=CTX)


def head_size(url):
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return int(r.headers.get("Content-Length") or 0)


def fetch_range(url, start, end, dest, tries=8):
    """把 url 的 [start, end] 字节断点续传到 dest。"""
    total = end - start + 1
    for t in range(tries):
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        if have >= total:
            return dest
        try:
            with urlopen(url, {"Range": f"bytes={start + have}-{end}"}, timeout=120) as r:
                if r.status != 206 and start + have > 0:
                    raise IOError(f"Range 未生效 (HTTP {r.status})")
                last = 0
                with open(dest, "ab") as f:
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        f.write(b)
                        have += len(b)
                        if time.time() - last > 8:
                            last = time.time()
                            log(f"    {os.path.basename(dest)} {have / 1048576:.1f}/{total / 1048576:.1f} MB")
        except Exception as e:
            log(f"    重试 {t + 1}/{tries}: {type(e).__name__} {e}")
            time.sleep(min(30, 2 ** t))
    if os.path.getsize(dest) < total:
        raise IOError(f"下载不完整: {dest}")
    return dest


def fetch(url, dest, magic=None):
    """整文件断点续传；magic 校验文件头（BTS 这类源会返回 200 + 错误页）。"""
    size = head_size(url)
    if size > LIMIT:
        raise SystemExit(f"{url} {size / 1048576:.0f}MB 超过单类 300MB 上限")
    if not (os.path.exists(dest) and os.path.getsize(dest) == size):
        log(f"  GET {url}  {size / 1048576:.1f} MB")
        fetch_range(url, 0, size - 1, dest)
    if magic:
        with open(dest, "rb") as f:
            if f.read(len(magic)) != magic:
                os.remove(dest)
                raise IOError(f"{dest} 不是预期格式（可能是错误页），已删除")
    return dest


def get_json(url, tries=5, pause=1.0, data=None, headers=None):
    """带 429 / 5xx 退避的 JSON 请求；404 返回 None。"""
    for t in range(tries):
        try:
            with urlopen(url, headers, timeout=60, data=data) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 or e.code >= 500:
                time.sleep(pause * 3 * (2 ** t))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(pause * (2 ** t))
    return None


class RemoteZip:
    """只靠 Range 请求读远程 zip：解析中央目录，按需取单个分片，不下整包。"""

    def __init__(self, url, cache_dir):
        self.url, self.cache = url, cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        size = head_size(url)
        n = min(size, 1 << 16)
        with urlopen(url, {"Range": f"bytes={size - n}-{size - 1}"}) as r:
            tail = r.read()
        i = tail.rfind(b"PK\x05\x06")
        cdsize, cdoff = struct.unpack("<II", tail[i + 12:i + 20])
        with urlopen(url, {"Range": f"bytes={cdoff}-{cdoff + cdsize - 1}"}) as r:
            cd = r.read()
        self.members, p = {}, 0
        while cd[p:p + 4] == b"PK\x01\x02":
            f = struct.unpack("<IHHHHHHIIIHHHHHII", cd[p:p + 46])
            name = cd[p + 46:p + 46 + f[10]].decode("utf-8", "replace")
            self.members[name] = (f[4], f[8], f[16])        # 压缩方法, 压缩字节, 本地头偏移
            p += 46 + f[10] + f[11] + f[12]

    def csize(self, name):
        return self.members[name][1]

    def fetch(self, name):
        method, csz, loff = self.members[name]
        with urlopen(self.url, {"Range": f"bytes={loff}-{loff + 29}"}) as r:
            nlen, elen = struct.unpack("<HH", r.read()[26:30])
        start = loff + 30 + nlen + elen
        dest = os.path.join(self.cache, name.replace("/", "__") + ".deflate")
        log(f"  分片 {name}  {csz / 1048576:.1f} MB")
        fetch_range(self.url, start, start + csz - 1, dest)
        return dest, method


def iter_lines(path, method=8):
    """流式解压 raw deflate 分片，逐行产出 str（去掉行尾）。"""
    z = zlib.decompressobj(-15) if method == 8 else None
    buf = b""
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            buf += z.decompress(b) if z else b
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                yield ln.rstrip(b"\r").decode("utf-8", "replace")
    if z:
        buf += z.flush()
    for ln in buf.split(b"\n"):
        if ln:
            yield ln.rstrip(b"\r").decode("utf-8", "replace")
