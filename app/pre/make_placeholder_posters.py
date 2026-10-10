#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_placeholder_posters.py —— 为「确实查不到海报」的影片生成标题卡底图。

为什么需要这一步
------------------------------------------------------------------
海报来自公网图源（维基 / AniList / iTunes …）。总有极少数影片在所有图源都没有条目：

    Nr. 24 (2024)              维基有词条但没有图片
    MST - Terra Prometida (2025)  2025 年巴西新片，无图源条目
    Mission Muh Dikhayi (2025)    同上（印地语新片）
    Attack on Titan: The Last Attack (2024)  维基只有剧集条目（无该剧场版海报）

前端虽然有「首字母 + 暂无海报」的兜底，但用户的要求是「榜单前 100 的海报必须正常显示」。
纯 DOM 色块在观感上仍像「没加载出来」。本脚本为这些缺口**生成一张真正的 JPEG 标题卡**：
深色渐变 + 片名 + 年份 + 「暂无海报」标记，风格与海报墙一致。

这样 `/assets/posters/<id>.jpg` 对这些影片也真实返回 200 + 图片，
前端不再有任何「加载失败」形态；同时卡面明确写着「暂无海报」，
不会被误认成真实海报——是「诚实的占位」而不是「伪造的素材」。

用法：
    python app/pre/make_placeholder_posters.py              # 只补缺口
    python app/pre/make_placeholder_posters.py --ids 1,2    # 指定 movieId
    python app/pre/make_placeholder_posters.py --all-missing  # 扫描 assets 目录的所有缺口
"""
import argparse
import hashlib
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # app/
PROJ = os.path.dirname(ROOT)
OUT = os.path.join(ROOT, "frontend", "assets", "posters")
W, H = 300, 450          # 2:3，与海报位一致
CJK_FONTS = [
    r"C:\Windows\Fonts\msyhbd.ttc",      # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]


def load_font(size):
    for p in CJK_FONTS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def hsl_rgb(h, s, l):
    """h∈[0,1), s/l∈[0,1] → (r,g,b)。用于和前端 hueOf(title) 一致的稳定配色。"""
    def f(n):
        k = (n + h * 12) % 12
        a = s * min(l, 1 - l)
        return int(255 * (l - a * max(-1, min(k - 3, 9 - k, 1))))
    return f(0), f(8), f(4)


def title_of(mid, title_map):
    return title_map.get(mid, str(mid))


def make_card(mid, title, year):
    """生成标题卡；配色由片名哈希决定（同片恒定）。"""
    h = (hashlib.md5(title.encode("utf-8")).digest()[0]) / 255.0
    top = hsl_rgb(h, 0.42, 0.26)
    bot = hsl_rgb((h + 0.10) % 1.0, 0.38, 0.10)
    im = Image.new("RGB", (W, H), bot)
    d = ImageDraw.Draw(im)
    # 竖向渐变
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)],
               fill=tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3)))
    # 胶片孔装饰（与前端海报位纹理呼应）
    for y in range(14, H - 14, 26):
        d.rounded_rectangle([7, y, 15, y + 13], radius=3, fill=(8, 11, 18))
        d.rounded_rectangle([W - 15, y, W - 7, y + 13], radius=3, fill=(8, 11, 18))
    # 片名分行的首字母大字（视觉锚点）
    initial = (title.strip()[:1] or "?").upper()
    f_big = load_font(150)
    bb = d.textbbox((0, 0), initial, font=f_big)
    d.text(((W - (bb[2] - bb[0])) / 2 - bb[0],
            H * 0.30 - (bb[3] - bb[1]) / 2 - bb[1]),
           initial, font=f_big, fill=(255, 255, 255, 40))
    # 片名（自动折行）
    f_t = load_font(19)
    words, lines, cur = title.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=f_t) <= W - 46 or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    lines = lines[:4]
    y = H - 108 - len(lines) * 24
    for ln in lines:
        wt = d.textlength(ln, font=f_t)
        d.text(((W - wt) / 2, y), ln, font=f_t, fill=(255, 255, 255))
        y += 24
    # 年份 + 明确标记
    f_s = load_font(13)
    tag = f"{year} · 暂无海报" if year else "暂无海报"
    wt = d.textlength(tag, font=f_s)
    d.text(((W - wt) / 2, H - 58), tag, font=f_s, fill=(210, 220, 235))
    d.line([(W * 0.22, H - 36), (W * 0.78, H - 36)], fill=(255, 255, 255, 60), width=1)
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="逗号分隔的 movieId")
    ap.add_argument("--all-missing", action="store_true",
                    help="扫描 app/data/movies.dat 与 assets 目录，补所有缺口")
    ap.add_argument("--limit", type=int, default=0, help="最多生成 N 张（0=不限）")
    a = ap.parse_args()

    # 读影片表
    title_map, year_map = {}, {}
    for ln in open(os.path.join(ROOT, "data", "movies.dat"), encoding="utf-8"):
        c = ln.rstrip("\n").split("\t")
        if len(c) < 4:
            continue
        try:
            mid = int(c[0])
        except ValueError:
            continue
        title_map[mid] = c[3]
        year_map[mid] = c[1]

    if a.ids:
        ids = [int(x) for x in a.ids.split(",") if x.strip()]
    elif a.all_missing:
        have = {int(f[:-4]) for f in os.listdir(OUT) if f.endswith(".jpg")}
        ids = [m for m in title_map if m not in have]
    else:
        p = os.path.join(PROJ, "_build_tmp", "top100_miss.json")
        if not os.path.exists(p):
            print(f"[错误] 缺 {p}；或加 --ids / --all-missing")
            return 1
        ids = json.load(open(p, encoding="utf-8"))
    if a.limit:
        ids = ids[:a.limit]

    print(f"生成占位标题卡 {len(ids)} 张 → {OUT}")
    n = 0
    for mid in ids:
        dest = os.path.join(OUT, f"{mid}.jpg")
        if os.path.exists(dest):
            continue
        t = title_of(mid, title_map)
        im = make_card(mid, t, year_map.get(mid, ""))
        im.save(dest, "JPEG", quality=88, optimize=True)
        n += 1
        print(f"  + {mid} {t[:52]}")
    print(f"完成：新生成 {n} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
