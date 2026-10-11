# -*- coding: utf-8 -*-
"""make_req6_html.py —— 把"实验内容 6"大规模基准表格注入网页性能基准页

用法（在工程根目录）：
    python tools/make_req6_html.py
数据源：results/results_req6.csv（由 build\\sort_req6.exe 生成）
注入位置：app/frontend/index.html 的 #p-bench 小节末尾（"应用（多场景）"锚点之前），
标记 <!-- REQ6:BENCH6:BEGIN --> … <!-- REQ6:BENCH6:END --> 之间可重复执行（幂等覆盖）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_req6_md import (load, cell, fmt_time, fmt_count, algo_tag,
                          SCALES, SCALE_LAB, DISTS, DIST_LAB, ALGOS,
                          COMPLEXITY, BASELINE, GROWTH_THEORY)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_IN = os.path.join(ROOT, "results", "results_req6.csv")
INDEX = os.path.join(ROOT, "app", "frontend", "index.html")

BEGIN = "<!-- REQ6:BENCH6:BEGIN -->"
END = "<!-- REQ6:BENCH6:END -->"


def bench_section_close(html):
    """#p-bench 小节的 </section> 位置（卡片必须落在小节内部，
    否则 .page 切换隐藏不了它，会在每个页都显示）"""
    idx = html.index('id="p-bench"')
    return html.index("</section>", idx)


def main():
    if not os.path.exists(CSV_IN):
        sys.exit(f"缺少 {CSV_IN}，请先运行 build\\sort_req6.exe")
    data, strategies = load(CSV_IN)
    card = build_card(data, strategies)
    block = f"{BEGIN}\n{card}\n{END}"

    with open(INDEX, encoding="utf-8") as f:
        html = f.read()

    if BEGIN in html and END in html:
        pre = html[:html.index(BEGIN)]
        post = html[html.index(END) + len(END):]
        html = pre + post
        action = "已移除旧位置内容"
    if BEGIN not in html and END not in html:
        pos = bench_section_close(html)
        html = html[:pos] + block + "\n" + html[pos:]
        action = "已插入 p-bench 小节末尾"

    with open(INDEX, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    print(f"{action}（{len(card)} 字符）")


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def td(v, cls=""):
    c = f' class="{cls}"' if cls else ""
    return f"<td{c}>{esc(v)}</td>"


def table_head(cols):
    return ("<thead><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr></thead>")


def time_table(data, d):
    out = ['<div class="scroll-tbl"><table class="b6-tbl">']
    out.append(table_head(["算法", "平均复杂度"] + [SCALE_LAB[n] for n in SCALES] +
                          ["增长倍率 t(3.2×10⁷)/t(10⁶)"]))
    out.append("<tbody>")
    for a in ALGOS:
        cls = "base" if a in BASELINE else ""
        trc = f' class="{cls}"' if cls else ""
        mk = "best" if a == "adapt" else ""   # AdaptSort 整行数据绿色
        row = [f'<td>{esc(algo_tag(a))}</td>',
               f'<td class="dim">{esc(COMPLEXITY[a])}</td>']
        for n in SCALES:
            v = cell(data, d, a, n, "tmin")
            row.append(td(fmt_time(v), mk or ("dim" if v is None else "")))
        t6, t32 = cell(data, d, a, 1000000, "tmin"), cell(data, d, a, 32000000, "tmin")
        ratio = f"×{t32 / t6:.1f}" if (t6 and t32 and t6 > 0 and t32 > 0) else "—"
        row.append(td(ratio, mk))
        out.append(f"<tr{trc}>" + "".join(row) + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def count_table(data, d, field, title):
    out = [f'<div class="b6-cap"><b>{title}</b>'
           f'<span class="b6-sub">（<span class="best">绿色</span> = AdaptSort）</span></div>',
           '<div class="scroll-tbl"><table class="b6-tbl">']
    out.append(table_head(["算法"] + [SCALE_LAB[n] for n in SCALES]))
    out.append("<tbody>")
    for a in ALGOS:
        cls = "base" if a in BASELINE else ""
        trc = f' class="{cls}"' if cls else ""
        mk = "best" if a == "adapt" else ""
        row = ["<td>" + esc(algo_tag(a)) + "</td>"]
        for n in SCALES:
            v = cell(data, d, a, n, field)
            row.append(td(fmt_count(v), mk or ("dim" if v is None or v < 0 else "")))
        out.append(f"<tr{trc}>" + "".join(row) + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def growth_table(data):
    out = ['<div class="b6-cap"><b>规模倍增实测 vs 理论</b><span class="b6-sub">'
           '（均匀分布；Θ(n²) 三算法以 10⁴→10⁵ 验证平方律；<span class="best">绿色</span> = '
           'AdaptSort）</span></div>',
           '<div class="scroll-tbl"><table class="b6-tbl">']
    out.append(table_head(["算法", "规模跨度", "理论时间倍率", "实测时间倍率", "实测比较倍率"]))
    out.append("<tbody>")
    for a in ALGOS:
        t6, t32 = cell(data, "uniform", a, 1000000, "tmin"), cell(data, "uniform", a, 32000000, "tmin")
        c6, c32 = cell(data, "uniform", a, 1000000, "cmp"), cell(data, "uniform", a, 32000000, "cmp")
        cls = "base" if a in BASELINE else ""
        trc = f' class="{cls}"' if cls else ""
        mk = "best" if a == "adapt" else ""
        if t6 and t32 and t6 > 0 and t32 > 0:
            span = "10⁶→3.2×10⁷（×32）"
            tr = f"×{t32 / t6:.1f}"
            cr = f"×{c32 / c6:.1f}" if c6 and c32 and c6 > 0 else "—"
        else:
            t4, t5 = cell(data, "uniform", a, 10000, "tmin"), cell(data, "uniform", a, 100000, "tmin")
            c4, c5 = cell(data, "uniform", a, 10000, "cmp"), cell(data, "uniform", a, 100000, "cmp")
            if not (t4 and t5 and t4 > 0):
                continue
            span = "10⁴→10⁵（×10）"
            tr = f"×{t5 / t4:.1f}"
            cr = f"×{c5 / c4:.1f}" if c4 and c5 and c4 > 0 else "—"
        row = [f"<td>{esc(algo_tag(a))}</td>", td(span), td(GROWTH_THEORY[a], "dim")]
        row.append(td(tr, mk))
        row.append(td(cr, mk))
        out.append(f"<tr{trc}>" + "".join(row) + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def dist_cmp_table(data, n, idx):
    out = [f'<div class="b6-cap"><b>同一规模 n={SCALE_LAB[n]} 下五分布的计算时间对比</b>'
           f'<span class="b6-sub">（ms；<span class="best">绿色</span> = AdaptSort）</span></div>',
           '<div class="scroll-tbl"><table class="b6-tbl">']
    out.append(table_head(["算法"] + [DIST_LAB[d] for d in DISTS]))
    out.append("<tbody>")
    for a in ALGOS:
        cls = "base" if a in BASELINE else ""
        trc = f' class="{cls}"' if cls else ""
        mk = "best" if a == "adapt" else ""
        row = [f"<td>{esc(algo_tag(a))}</td>"]
        for d in DISTS:
            v = cell(data, d, a, n, "tmin")
            row.append(td(fmt_time(v), mk or ("dim" if v is None else "")))
        out.append(f"<tr{trc}>" + "".join(row) + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def strategy_table(strategies):
    out = ['<div class="b6-cap"><b>AdaptSort 策略选择</b><span class="b6-sub">'
           '（特征采样 → 决策函数 → 执行策略）</span></div>',
           '<div class="scroll-tbl"><table class="b6-tbl">']
    out.append(table_head(["分布", "n=10⁵", "n=10⁶", "n=3.2×10⁷"]))
    out.append("<tbody>")
    for d in DISTS:
        row = [f"<td>{esc(DIST_LAB[d])}</td>"]
        for n in (100000, 1000000, 32000000):
            row.append(td(strategies.get((d, n), "—")))
        out.append("<tr>" + "".join(row) + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def build_card(data, strategies):
    parts = []
    parts.append('<div class="card" id="b6">')
    parts.append('<h2>大规模性能基准 · 五类数据形态 × 五档数据量（实验内容 6）</h2>')
    parts.append('<div class="note">8 种自实现算法与 AdaptSort / std::sort（基线）在'
                 '<b>正序、逆序、均匀、高斯、泊松</b>五类数据上、10⁴→3.2×10⁷ 五档数据量下的实测：'
                 '比较次数、移动次数、计算时间三指标分离测量，QPC 计时取轮次最小值，固定种子可复现'
                 '（10⁴~10⁶ 计数与全矩阵实验 90/90 逐一一致）；顶档 3.2×10⁷ = CineRank 的 '
                 'MovieLens 全量评分排序负载。<span class="dim">Θ(n²) 三算法在 10⁵ 以上仅正序档实跑'
                 '（O(n) 特例），其余记 —。</span></div>')
    parts.append('<div class="keylab" id="b6-chips">')
    for i, d in enumerate(DISTS):
        on = " on" if i == 0 else ""
        parts.append(f'<span class="kl-chip{on}" data-d="{d}">{DIST_LAB[d]}</span>')
    parts.append('</div>')
    parts.append('<div id="b6-grps">')
    for i, d in enumerate(DISTS):
        disp = "" if i == 0 else ' style="display:none"'
        parts.append(f'<div class="b6-grp" data-d="{d}"{disp}>')
        parts.append('<div class="b6-cap"><b>计算时间</b><span class="b6-sub">'
                     '（ms，同列取重复轮最小值；<span class="best">绿色</span> = AdaptSort）</span></div>')
        parts.append(time_table(data, d))
        parts.append(count_table(data, d, "cmp", "比较次数"))
        parts.append(count_table(data, d, "mv", "移动次数"))
        parts.append('</div>')
    parts.append('</div>')
    parts.append('<div class="b6-div"></div>')
    parts.append(growth_table(data))
    parts.append(dist_cmp_table(data, 1000000, 2))
    parts.append(dist_cmp_table(data, 32000000, 3))
    parts.append(strategy_table(strategies))
    parts.append('<div class="b6-sum"><b>总结</b>　数据量 ×32（10⁶→3.2×10⁷）时 n log n 组'
                 '时间增长 ×36~×47、贴合理论 ×40，堆排/希尔因访存模式超出至 ×78/×51；'
                 '泊松的高重复键使三路快排比较骤降 82%；正序输入让插入/冒泡在 3.2×10⁷ 档'
                 '仍只需 38/49ms（同规模随机数据外推 29 分钟~7.7 小时）——数据形态与规模'
                 '共同决定选型，O(n²) 在大数据量下出局，AdaptSort 五分布全部第一。</div>')
    parts.append('</div>')
    return "\n".join(parts)


if __name__ == "__main__":
    main()
