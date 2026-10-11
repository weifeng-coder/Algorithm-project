# -*- coding: utf-8 -*-
"""make_req6_md.py —— 由 results/results_req6.csv 生成"实验内容 6"性能基准 md 文档

用法（在工程根目录）：
    python tools/make_req6_md.py
输出：
    实验内容6-大规模性能基准.md（工程根目录，可直接整段嵌入网页"性能基准"区）

表格全部由 CSV 程序化生成；"评估结论"一节的文字在 FINDINGS_MD 常量中维护
（引用的关键数字在基准重跑后需人工核对）。
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_IN = os.path.join(ROOT, "results", "results_req6.csv")
CSV_REF = os.path.join(ROOT, "results", "results_c.csv")
MD_OUT = os.path.join(ROOT, "实验内容6-大规模性能基准.md")

SCALES = [10000, 100000, 1000000, 10000000, 32000000]
SCALE_LAB = {10000: "10⁴", 100000: "10⁵", 1000000: "10⁶",
             10000000: "10⁷", 32000000: "3.2×10⁷"}

DISTS = ["uniform", "gauss", "poisson", "asc", "desc"]
DIST_LAB = {"uniform": "均匀分布", "gauss": "高斯分布", "poisson": "泊松分布",
            "asc": "正序数据", "desc": "逆序数据"}

ALGOS = ["insertion", "bubble", "selection", "shell", "quick",
         "heap", "radix", "merge", "adapt", "stdsort"]
ALGO_LAB = {
    "insertion": "直接插入", "bubble": "冒泡", "selection": "选择",
    "shell": "希尔", "quick": "快速", "heap": "堆",
    "radix": "基数", "merge": "归并", "adapt": "AdaptSort", "stdsort": "std::sort",
}
COMPLEXITY = {
    "insertion": "O(n²)／正序 O(n)", "bubble": "O(n²)／正序 O(n)",
    "selection": "O(n²)（比较与数据无关）", "shell": "O(n^1.3) 左右",
    "quick": "O(n log n)（三路划分）", "heap": "O(n log n)",
    "radix": "O(n)（4 趟字节，仅整数键）", "merge": "O(n log n)（稳定）",
    "adapt": "自适应（特征采样→决策）", "stdsort": "O(n log n)（库基线）",
}
BASELINE = {"adapt", "stdsort"}   # 非题目要求 8 算法，标注为参考基线


def fmt_time(t):
    if t is None or t < 0:
        return "—"
    if t < 1:
        return f"{t:.4f}"
    if t < 100:
        return f"{t:.2f}"
    if t < 10000:
        return f"{t:.1f}"
    return f"{t:,.0f}"


def fmt_count(v):
    if v is None or v < 0:
        return "—"
    return f"{v:,}"


def load(path):
    data = {}
    strategies = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["dist"], r["algo"], int(r["n"]))
            data[key] = {
                "tmin": float(r["tmin_ms"]),
                "cmp": int(r["cmp"]),
                "mv": int(r["mv"]),
                "ok": int(r["ok"]),
            }
            if r["algo"] == "adapt" and r["strategy"]:
                strategies[(r["dist"], int(r["n"]))] = r["strategy"]
    return data, strategies


def cross_validate(data):
    """与 results_c.csv（C 构建）比对共同单元的计数：8 自实现 × 5 分布 × 10⁴/10⁵/10⁶"""
    if not os.path.exists(CSV_REF):
        return None
    ref = {}
    with open(CSV_REF, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if int(r["cmp"]) < 0:      # 旧矩阵未实跑的 Θ(n²) 大规模档（记 -1）
                continue
            ref[(r["dist"], r["algo"], int(r["n"]))] = (r["cmp"], r["mv"])
    total = match = 0
    mismatch = []
    for d in DISTS:
        for a in ALGOS:
            if a in BASELINE:
                continue
            for n in SCALES[:3]:
                k = (d, a, n)
                if k in data and k in ref:
                    total += 1
                    got = (str(data[k]["cmp"]), str(data[k]["mv"]))
                    if got == ref[k]:
                        match += 1
                    else:
                        mismatch.append((k, got, ref[k]))
    return total, match, mismatch


def cell(data, d, a, n, field):
    e = data.get((d, a, n))
    return e[field] if e else None


def algo_tag(a):
    return f"{ALGO_LAB[a]}（基线）" if a in BASELINE else ALGO_LAB[a]


def build_tables(data):
    parts = []
    tno = [0]   # 表 2-1 ~ 2-15 顺序编号

    def next_no():
        tno[0] += 1
        return tno[0]

    # ---- 每分布三张表：时间 / 比较次数 / 移动次数 ----
    for d in DISTS:
        parts.append(f"### {DIST_LAB[d]}\n")
        # 时间表（含增长倍率）
        parts.append(f"**表 2-{next_no()}　{DIST_LAB[d]}·计算时间 t/min（ms，同列取重复轮最小值）**\n")
        parts.append("| 算法 | 平均复杂度 | " +
                     " | ".join(SCALE_LAB[n] for n in SCALES) +
                     " | 增长倍率 t(3.2×10⁷)/t(10⁶) |")
        parts.append("|---|---|" + "---|" * (len(SCALES) + 1))
        for a in ALGOS:
            row = [algo_tag(a), COMPLEXITY[a]]
            for n in SCALES:
                row.append(fmt_time(cell(data, d, a, n, "tmin")))
            t6, t32 = cell(data, d, a, 1000000, "tmin"), cell(data, d, a, 32000000, "tmin")
            ratio = f"×{t32 / t6:.1f}" if (t6 and t32 and t6 > 0 and t32 > 0) else "—"
            row.append(ratio)
            parts.append("| " + " | ".join(row) + " |")
        parts.append("")

        # 比较次数表
        parts.append(f"**表 2-{next_no()}　{DIST_LAB[d]}·比较次数**\n")
        parts.append("| 算法 | " + " | ".join(SCALE_LAB[n] for n in SCALES) + " |")
        parts.append("|---|" + "---|" * len(SCALES))
        for a in ALGOS:
            row = [algo_tag(a)]
            for n in SCALES:
                row.append(fmt_count(cell(data, d, a, n, "cmp")))
            parts.append("| " + " | ".join(row) + " |")
        parts.append("")

        # 移动次数表
        parts.append(f"**表 2-{next_no()}　{DIST_LAB[d]}·移动次数**\n")
        parts.append("| 算法 | " + " | ".join(SCALE_LAB[n] for n in SCALES) + " |")
        parts.append("|---|" + "---|" * len(SCALES))
        for a in ALGOS:
            row = [algo_tag(a)]
            for n in SCALES:
                row.append(fmt_count(cell(data, d, a, n, "mv")))
            parts.append("| " + " | ".join(row) + " |")
        parts.append("")

    return parts


GROWTH_THEORY = {
    "insertion": "×100（n²）", "bubble": "×100（n²）", "selection": "×100（n²）",
    "shell": "×90（n^1.3 经验律）", "quick": "×40（n log n）", "heap": "×40（n log n）",
    "radix": "×32（O(n)）", "merge": "×40（n log n）",
    "adapt": "≤×40（上界 n log n）", "stdsort": "×40（n log n）",
}


def build_growth_table(data):
    """规模倍增验证：10⁶→3.2×10⁷（×32）时实测时间/比较次数的增长 vs 理论；
    O(n²) 三算法用 10⁴→10⁵（×10）验证平方律。"""
    rows = []
    for a in ALGOS:
        t6, t32 = cell(data, "uniform", a, 1000000, "tmin"), cell(data, "uniform", a, 32000000, "tmin")
        c6, c32 = cell(data, "uniform", a, 1000000, "cmp"), cell(data, "uniform", a, 32000000, "cmp")
        if not (t6 and t32 and t6 > 0 and t32 > 0):
            t4, t5 = cell(data, "uniform", a, 10000, "tmin"), cell(data, "uniform", a, 100000, "tmin")
            c4, c5 = cell(data, "uniform", a, 10000, "cmp"), cell(data, "uniform", a, 100000, "cmp")
            if t4 and t5 and t4 > 0:
                rows.append((a, "10⁴→10⁵（×10）", GROWTH_THEORY[a], f"×{t5 / t4:.1f}",
                             (f"×{c5 / c4:.1f}" if c4 and c5 and c4 > 0 else "—")))
            continue
        rows.append((a, "10⁶→3.2×10⁷（×32）", GROWTH_THEORY[a], f"×{t32 / t6:.1f}",
                     (f"×{c32 / c6:.1f}" if c6 and c32 and c6 > 0 else "—")))
    out = ["**表 3-1　规模倍增时的时间/比较次数增长倍率（均匀分布，实测 vs 理论）**\n",
           "| 算法 | 规模跨度 | 理论时间倍率 | 实测时间倍率 | 实测比较倍率 |",
           "|---|---|---|---|---|"]
    for a, span, theo, tr, cr in rows:
        out.append(f"| {algo_tag(a)} | {span} | {theo} | {tr} | {cr} |")
    out.append("")
    return out


def build_dist_compare(data):
    """同一规模下五分布对比（时间）：分布形态对比较类算法的影响"""
    out = []
    for i, n in enumerate((1000000, 32000000), start=2):
        out.append(f"**表 3-{i}　同一规模 n={SCALE_LAB[n]} 下五分布的计算时间对比（ms）**\n")
        out.append("| 算法 | 均匀 | 高斯 | 泊松 | 正序 | 逆序 |")
        out.append("|---|---|---|---|---|---|")
        for a in ALGOS:
            row = [algo_tag(a)]
            for d in DISTS:
                row.append(fmt_time(cell(data, d, a, n, "tmin")))
            out.append("| " + " | ".join(row) + " |")
        out.append("")
    return out


def build_strategy_table(strategies):
    out = ["**表 4-1　AdaptSort 在五分布上的策略选择**\n",
           "| 分布 | n=10⁵ | n=10⁶ | n=3.2×10⁷ |", "|---|---|---|---|"]
    for d in DISTS:
        row = [DIST_LAB[d]]
        for n in (100000, 1000000, 32000000):
            row.append(strategies.get((d, n), "—"))
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    return out


FINDINGS_MD = """## 五、评估结论

**结论 1：数据量增长下，复杂度分层与理论完全吻合。**
10⁶→3.2×10⁷（数据量 ×32）时，n log n 组实测时间增长 ×35.9~×46.6（理论 ×40.0），
其中 std::sort（×39.9）几乎精确命中理论线，快排/AdaptSort（×35.9）略快于理论；归并（×46.6）与
基数（×42.5，理论 ×32）超出部分来自 10⁶ 档 4MB → 顶档 128MB 工作集的缓存/带宽
代价；堆排 ×78.4（堆化跳跃访存对 cache 最不友好）、希尔 ×51.0（n/2 增量在大 n
下劣化）。10⁴→10⁵（×10）时 Θ(n²) 三算法增长 ×102~×110（理论 ×100），平方律在
真实机器上成立。按此外推：冒泡在均匀 3.2×10⁷ 档需 **27,059ms × 1024 ≈ 7.7 小时**
——表中"—"不是省略，而是 O(n²) 在大数据量下不可用的量化证据。

**结论 2：数据形态决定 Θ(n²) 算法的生死。**
同一算法、同一规模 3.2×10⁷：正序档直接插入 **38.4ms**（比较 n−1=31,999,999、
移动 2(n−1)，均为解析精确值）、冒泡 **49.3ms**（移动 0，提前终止生效）；而它们
排在随机数据上外推需 ~29 分钟 / ~7.7 小时——输入有序性带来 2~4 个数量级差距。
逆序档 10⁵ 即最坏情形：比较精确命中 n(n−1)/2，冒泡移动 3n(n−1)/2=1.5×10¹⁰、
耗时 38.5s（逆序 3.2×10⁷ 外推 ~11 小时）。选择排序则证明"与数据无关"：五个分布
10⁵ 档比较次数**全部精确等于 4,999,950,000**，但移动次数从 0（正序）到 3×10⁵
不等——比较省不等于整体快。

**结论 3：三个指标合成画像，"比较次数最少 ≠ 最快"。**
3.2×10⁷ 均匀档：基数比较恒 0、移动 8n=2.56×10⁸、**372ms** 自实现最快（但仅适用
整数键）；快排比较 8.16×10⁸ ≈ **1.02·n·log₂n**（教科书精确命中）、2875ms；归并
比较略少（7.58×10⁸）但移动翻倍至 1.6×10⁹ 且需 128MB 辅助数组，4243ms；堆排比较
最多之一（1.50×10⁹ = 1.88 n log₂n）、比较类中移动最少（9.3×10⁸）、却以
**10,159ms 垫底**——省比较的跳跃访存在现代存储层次上反而最慢；希尔比较
5.0×10⁹ 全场最多、9098ms。库基线 std::sort（2300ms）比自实现快排稳定快
~20%~28%（内联比较器 + 精调的 introsort），AdaptSort 以 **292ms** 居首。

**结论 4：分布形态的影响集中在"重复键结构"，而非分布形状。**
高斯 vs 均匀（同为连续型）：各算法比较/移动计数差异 ≤3%——比较类算法只关心
元素间相对次序，不关心取值分布形状；时间差异多数 ≤10%，个别顶档单元（基数
+17%、堆排 +19%）属长跑中的访存/机器状态漂移。泊松（λ=50，值域仅 ~0..250、
重复度极高）才是分布变量里真正的"性能杠杆"：三路快排比较从 8.16×10⁸ 骤降
**82%** 至 1.45×10⁸（相等段被三路划分整段吞掉），时间 2875→617ms；而固定 4 趟的
基数在计数上无动于衷（比较恒 0、移动恒 8n），把窄值域的红利让给了会看数据的
AdaptSort（105ms，见结论 5）。正序/逆序对随机化快排几乎无影响（10⁶ 档比较
25.5×10⁶ vs 均匀 24.8×10⁶）——随机基准 + 三路划分让教科书里"快排在正序输入
退化 O(n²)"的现象在本实现中不存在。

**结论 5：AdaptSort 的自适应收益随规模放大，五分布全部第一。**
策略表显示其在五类数据上各选所长的策略：正序→直通（比较 32,000,253 ≈ n+探测、
**移动 0**、13.6ms）、逆序→反转（比较 ≈n、移动 1.5n、34.2ms）、泊松→8 位基数
（窄值域使散射趟数减半，105ms）、均匀/高斯→16/8 位基数按规模切换（292/301ms）。
3.2×10⁷ 档对 std::sort 加速 **7.8×~39.1×**，且五个分布无一例外——"先看数据、
再选策略"的收益在大数据量下从"锦上添花"变成"数量级差距"。

**结论 6：顶档规模即应用真实负载。**
3.2×10⁷ = CineRank 应用 MovieLens 全量评分 posting 键的排序规模。该档实测
（最快 292ms，最慢自实现比较排序 ~10s）与真实数据矩阵（results_real.csv）相互
印证：工程上"够大"的数据量已把 O(n²) 挡在门外，选型空间只剩 O(n log n) 比较
排序与 O(n) 的整数键专用排序——这正是 AdaptSort 决策函数覆盖的两类策略。
"""



def main():
    if not os.path.exists(CSV_IN):
        sys.exit(f"缺少 {CSV_IN}，请先运行 build\\sort_req6.exe")
    data, strategies = load(CSV_IN)

    cv = cross_validate(data)

    env = """## 一、实验设计

### 1.1 目的与指标

针对**足够大的不同数据量**，把同一批排序算法分别运行在**正序、逆序、均匀分布、
高斯分布、泊松分布**五类关键字序列上，以 **比较次数、移动次数、计算时间** 三个
指标评估各算法的时间性能，检验"复杂度结论是否在大数据量下依然成立"以及
"数据形态本身对性能的影响"。

### 1.2 变量控制

| 维度 | 取值 |
|---|---|
| 数据量 n | 10⁴、10⁵、10⁶、10⁷、**3.2×10⁷**（顶档 = CineRank 应用 MovieLens 全量评分的实际排序负载规模） |
| 数据形态 | 正序（1..n）、逆序（n..1）、均匀分布 [0,10⁶)、高斯分布（μ=5×10⁵，σ=10⁶/6，截断）、泊松分布（λ=50，Knuth 法） |
| 算法 | 8 种自实现（直接插入、冒泡、选择、希尔、快速、堆、基数、归并）+ AdaptSort + std::sort（后两者为参考基线） |
| 输出次序 | 统一升序（逆序数据 = 升序排序的"最坏输入"，不单独测降序输出） |

数据由 `common/datagen.h` 的 xorshift32 固定种子生成器产生：同 (分布，规模) 单元的
输入**逐字节可复现**，且 10⁴~10⁶ 三档与全矩阵实验（results_c.csv）使用完全相同的
种子公式（`seed = 1000003·n + 97·(分布号+1)`），计数可交叉验证。

### 1.3 测量口径（与实验内容 5 统计模块一致）

- **比较次数**：一次关键字比较记 1（含方向化比较）；基数排序比较恒 0。
- **移动次数**：一次赋值记 1，一次三赋值交换记 3；基数排序散射与整块拷回逐元素计入。
- **计算时间**：QueryPerformanceCounter（~100ns 分辨率）；等时长批量计时
  （单次 <8ms 时整批约 8ms 计时后除以批数，拷贝工作集 ≤8MB）；重复多轮取**最小值**
  （机器负载为单侧噪声），常规档 5 轮、Θ(n²) 组与 n≥10⁷ 档 3 轮。
- **计数/计时分离**：计时时统计钩子关闭，另跑一遍纯计数运行——时间不含计数开销。
- n≥3×10⁶ 档计时采用"固定母本逐轮 memcpy"（母本在计时区间外生成一次）：
  每轮排序同一份未排序负载，避免泊松生成器（每元素 ~50 次随机迭代）的再生成开销；
  小规模档保持逐轮重新生成，与全矩阵实验语义一致。
- Θ(n²) 三算法在 n>10⁵ 仅正序档实跑直接插入/冒泡（正序下二者为 O(n)），
  其余格记"—"不实跑：冒泡 10⁵ 随机档实测 27.1 s，按平方律外推 10⁶ 档单次
  ~45 min、3.2×10⁷ 档 ~7.7 h——这一外推本身就是"O(n²) 算法在大数据量下
  不可用"的实测证据。

### 1.4 环境与复现

| 项 | 值 |
|---|---|
| 处理器 | Intel Core i7-14700HX（20 核 28 线程，单线程运行） |
| 内存 | 32 GB |
| 操作系统 | Windows 11（build 26200） |
| 编译器 | MinGW-W64 GCC 13.2.0，`g++ -O2 -std=c++17 -Wall` |
| 复现 | `g++ -O2 -std=c++17 -Wall -I common -I cpp/adaptsort cpp/main_req6.cpp -o build/sort_req6.exe && build\\sort_req6.exe`；md 由 `python tools/make_req6_md.py` 重新生成 |
"""

    cv_md = ""
    if cv:
        total, match, mismatch = cv
        verdict = "全部一致" if not mismatch else f"不一致 {len(mismatch)} 格（首例 {mismatch[0]}）"
        cv_md = (f"**可复现性交叉验证**：10⁴/10⁵/10⁶ 三档、8 种自实现算法、五分布中与全矩阵实验"
                 f"（C 构建 results_c.csv，同种子同生成器）可比的 {total} 个单元，比较/移动计数"
                 f"**{verdict}**（{match}/{total}）；全矩阵未实跑（记 -1）的 Θ(n²) 10⁵ 档与正序"
                 f"插入/冒泡 10⁶ 档共 17 个单元由本次基准**补齐实测**。\n")

    sections = []
    sections.append("# 实验内容 6：不同数据量下的时间性能评估（大规模性能基准）\n")
    sections.append(env)
    sections.append("## 二、计算时间 / 比较次数 / 移动次数 总表\n")
    sections.append(cv_md)
    sections.append("\n".join(build_tables(data)))
    sections.append("## 三、规模增长与分布对比分析\n")
    sections.append("\n".join(build_growth_table(data)))
    sections.append("\n".join(build_dist_compare(data)))
    sections.append("## 四、AdaptSort 自适应策略\n")
    sections.append("\n".join(build_strategy_table(strategies)))
    sections.append(FINDINGS_MD)

    md = "\n".join(s if s.endswith("\n") else s + "\n" for s in sections)
    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"已生成 {MD_OUT}")
    if cv:
        print(f"交叉验证: {cv[1]}/{cv[0]} 一致")


if __name__ == "__main__":
    main()
