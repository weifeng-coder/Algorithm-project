#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download poster thumbnails for current Top-K movies (iTunes + Wikipedia REST)."""
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "frontend", "assets", "posters")
UA = {
    "User-Agent": "CineRank-course/1.0 (educational; poster cache)",
    "Accept": "application/json,image/*",
}
CTX = ssl.create_default_context()
ALIASES = {
    "Seven (a.k.a. Se7en)": "Se7en",
    "Raiders of the Lost Ark (Indiana Jones and the Raiders of the Lost Ark)": "Raiders of the Lost Ark",
    "Star Wars: Episode IV - A New Hope": "Star Wars",
    "Star Wars: Episode V - The Empire Strikes Back": "The Empire Strikes Back",
    "Star Wars: Episode VI - Return of the Jedi": "Return of the Jedi",
    "Terminator 2: Judgment Day": "Terminator 2: Judgment Day",
    "The Lord of the Rings: The Fellowship of the Ring": "The Lord of the Rings: The Fellowship of the Ring",
    "The Lord of the Rings: The Two Towers": "The Lord of the Rings: The Two Towers",
    "The Lord of the Rings: The Return of the King": "The Lord of the Rings: The Return of the King",
    # 非英语片：Wikipedia 词条用英文通名，需按原名/惯用译名补别名
    "Spirited Away (Sen to Chihiro no kamikakushi)": "Spirited Away",
    "City of God (Cidade de Deus)": "City of God (2002 film)",
    "Princess Mononoke (Mononoke-hime)": "Princess Mononoke",
    "Seven Samurai (Shichinin no samurai)": "Seven Samurai",
    "My Neighbor Totoro (Tonari no Totoro)": "My Neighbor Totoro",
    "Lives of Others, The (Das leben der Anderen)": "The Lives of Others",
    "Come and See (Idi i smotri)": "Come and See",
    "Harakiri (Seppuku)": "Harakiri (1962 film)",
    "High and Low (Tengoku to jigoku)": "High and Low (1963 film)",
    "Whiplash": "Whiplash (2014 film)",
    "Band of Brothers": "Band of Brothers (miniseries)",
    "Twin Peaks": "Twin Peaks",
    "Black Mirror": "Black Mirror",
}


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read(), r.headers.get("Content-Type", "")


def clean_title(title):
    t = re.sub(r"\s*\(\d{4}\)\s*$", "", title or "").strip()
    t = ALIASES.get(t, t)
    m = re.match(r"^(.*),\s*(The|A|An)$", t, re.I)
    if m:
        t = m.group(2) + " " + m.group(1)
    t = re.sub(r"\s*\(a\.k\.a\..*$", "", t).strip()
    return t


def itunes_art(title):
    q = urllib.parse.urlencode(
        {"term": title, "media": "movie", "entity": "movie", "limit": 5, "country": "US"}
    )
    raw, _ = fetch("https://itunes.apple.com/search?" + q)
    data = json.loads(raw.decode("utf-8"))
    want = re.sub(r"[^a-z0-9]+", "", title.lower())
    best = None
    for r in data.get("results") or []:
        name = r.get("trackName") or r.get("collectionName") or ""
        art = r.get("artworkUrl100") or r.get("artworkUrl60")
        if not art:
            continue
        art = art.replace("100x100bb", "300x300bb").replace("60x60bb", "300x300bb")
        key = re.sub(r"[^a-z0-9]+", "", name.lower())
        if want and (want in key or key in want):
            return art
        if best is None:
            best = art
    return best


def wiki_art(title):
    cands = [title, title + " (film)", title + " (miniseries)",
             title + " (TV series)", title + " (1994 film)", title + " (1993 film)"]
    seen = set()
    for t in cands:
        if t in seen:
            continue
        seen.add(t)
        slug = urllib.parse.quote(t.replace(" ", "_"))
        url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + slug
        try:
            raw, _ = fetch(url)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                continue
            if e.code == 429:
                time.sleep(2.0)
                continue
            raise
        data = json.loads(raw.decode("utf-8"))
        src = ((data.get("originalimage") or data.get("thumbnail") or {}).get("source"))
        if src:
            return src
    return None


def save_img(url, dest):
    raw, ctype = fetch(url)
    if not raw or len(raw) < 800:
        return False
    if "jpeg" not in ctype and "jpg" not in ctype and "png" not in ctype and "webp" not in ctype:
        if raw[:3] != b"\xff\xd8\xff" and raw[:8] != b"\x89PNG\r\n\x1a\n":
            return False
    with open(dest, "wb") as f:
        f.write(raw)
    return True


def main():
    os.makedirs(OUT, exist_ok=True)
    items = []
    # 三种排序键各取 Top-50：榜单随数据/模式变化，海报缓存要跟着覆盖
    for mode in ("pop", "rat", "bayes"):
        url = f"http://127.0.0.1:8080/api/movies/topk?k=50&genre=(all)&minvotes=1000&mode={mode}"
        raw, _ = fetch(url)
        items.extend(json.loads(raw.decode("utf-8")).get("items") or [])
    seen, uniq = set(), []
    for m in items:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        uniq.append(m)
    print("movies", len(uniq))
    ok = miss = 0
    for m in uniq:
        dest = os.path.join(OUT, f"{m['id']}.jpg")
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            print(" have", m["id"], m["title"])
            ok += 1
            continue
        title = clean_title(m["title"])
        src = None
        try:
            src = itunes_art(title)
        except Exception as e:
            print(" itunes fail", m["id"], type(e).__name__, e)
        if not src:
            try:
                src = wiki_art(title)
            except Exception as e:
                print(" wiki fail", m["id"], type(e).__name__, e)
        if not src:
            print(" miss", m["id"], title)
            miss += 1
            time.sleep(0.4)
            continue
        try:
            if save_img(src, dest):
                print(" got", m["id"], title, os.path.getsize(dest))
                ok += 1
            else:
                print(" bad", m["id"], src)
                miss += 1
        except Exception as e:
            print(" save fail", m["id"], type(e).__name__, e)
            miss += 1
        time.sleep(0.45)
    print("done have", ok, "miss", miss, "->", OUT)


if __name__ == "__main__":
    main()
