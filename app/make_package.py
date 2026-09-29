#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_package.py —— 生成转发用清理包（排除全量数据与编译产物）"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（app/ 的上一级）
OUT = os.path.join(os.path.dirname(ROOT), "排序算法-统一版-20260928.zip")
EXCLUDE = {os.path.join("app", "data", "postings.dat").replace(os.sep, "/"),
           os.path.join("app", "data", "web", "postings.dat").replace(os.sep, "/"),
           os.path.join("app", "data", "amazon", "postings.dat").replace(os.sep, "/"),
           os.path.join("app", "data", "bike", "postings.dat").replace(os.sep, "/"),
           os.path.join("app", "data", "bike", "ledger.dat").replace(os.sep, "/"),
           os.path.join("app", "data", "gene", "postings.dat").replace(os.sep, "/"),
           os.path.join("app", "data", "gene", "sa.dat").replace(os.sep, "/"),
           os.path.join("app", "data", "web", "score.dat").replace(os.sep, "/"),
           os.path.join("app", "data", "flight", "postings.dat").replace(os.sep, "/"),
           os.path.join("app", "data", "flight", "ledger.dat").replace(os.sep, "/")}
SKIP_DIRS = {"__pycache__", "build", "downloads"}

def main():
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in sorted(files):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                if rel in EXCLUDE:
                    continue
                z.write(full, "排序算法/" + rel)
                n += 1
    print("files:", n)
    print("size: %.1f MB" % (os.path.getsize(OUT) / 1048576))
    print("out:", OUT)

if __name__ == "__main__":
    main()
