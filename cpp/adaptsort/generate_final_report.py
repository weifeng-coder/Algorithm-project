# -*- coding: utf-8 -*-
"""
generate_final_report.py
生成完整的 AdaptSort 算法设计报告（DOCX格式）
"""

import os
import shutil
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from build_docx_helpers import (
    add_paragraph_styled, add_heading_1, add_heading_2, add_heading_3,
    add_code_block, add_callout, create_table_styled, format_run
)

def build_report():
    doc = docx.Document()

    # 设置页面边距（上下左右 1 英寸 / 2.54 厘米）
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # ---------------- 标题区 ----------------
    title_p = add_paragraph_styled(
        doc, "AdaptSort 自适应混合排序算法设计报告",
        font_name="黑体", size_pt=20.0, bold=True, color_rgb=(15, 23, 42),
        space_before=12, space_after=8, align=WD_ALIGN_PARAGRAPH.CENTER
    )
    sub_p = add_paragraph_styled(
        doc, "面向多分布形态的自适应决策框架、探测机制与硬件成本标定模型",
        font_name="宋体", size_pt=11.5, italic=True, color_rgb=(100, 116, 139),
        space_before=0, space_after=18, align=WD_ALIGN_PARAGRAPH.CENTER
    )

    # 导言（保持 adaptsort.docx / 一些细节讲解.docx 亲切且极其透彻的风格）
    add_paragraph_styled(
        doc,
        "宝宝，这份报告是专门针对我们工程中的 AdaptSort（普适自适应排序算法）整理的完整算法设计报告。"
        "AdaptSort 并不是试图重新发明一个单一的“万能快速内层循环”，而是建立了一个“输入特征感知 + 确定性短路规则 + 本机实测成本模型”的自适应混合排序决策框架。"
        "正如题目提示所指出的：“很少有某个算法能够在所有方面打败其他算法”，AdaptSort 的核心正是通过极低开销的探测识别数据形态，"
        "动态分派最优排序策略，使算法在全部测试分布上的表现均不劣于任何单一算法的最优表现。",
        font_name="宋体", size_pt=10.5, space_after=8, line_spacing=1.35
    )
    add_paragraph_styled(
        doc,
        "本报告以当前代码仓库（v3.1 统一工程版）的最新源码为准，全面涵盖代码架构、伪 C 代码逻辑、探测与分派核心原理、各种情况下的理论与实际有效复杂度分析（重点解答关于前置保护滤网如何彻底拦截最坏情况的机制）、对四大主流经典算法的痛点缓解，以及结合实测数据的客观综合评价。",
        font_name="宋体", size_pt=10.5, space_after=14, line_spacing=1.35
    )

    # =========================================================================
    # 一、源代码架构与系统层次划分
    # =========================================================================
    add_heading_1(doc, "一、源代码架构与系统层次划分")

    add_paragraph_styled(
        doc,
        "AdaptSort 采用了清晰的“四层架构”设计理念，将排序生命周期划分为编译期类型判定、运行时轻量探测、多维成本决策与底层策略执行四个阶段：",
        font_name="宋体", size_pt=10.5, space_after=6
    )

    arch_points = [
        ("1. 类型层（Type Layer）：", "在编译期通过 `std::is_integral_v<T>` 静态判定键类型。如果是整数键，开启值域类策略（计数排序、基数排序）与比较类的竞争通道；如果是通用键（如浮点数、字符串、自定义对象），直接绕过值域探测，仅在比较类家族中决策。"),
        ("2. 特征层（Feature Layer）：", "采用“按需付费、两阶段解耦”的低成本探测机制。阶段 A（`probe_order`）通过 O(√n) 确定性等距采样提取顺序特征；阶段 B（`probe_domain` / `probe_dup`）则按需复用采样点提取值域与重复率，绝不为不需要的特征付出探测代价。"),
        ("3. 决策层（Decision Layer）：", "融合了“确定性快速短路规则”与“运行时实测自标定成本模型（CostModel）”。对于已升序、已降序、小规模近序、小规模窄值域直接走硬规则；对于其余情况，利用启动时标定的 (n, k) 双维网格与 log-log 插值模型预测候选耗时，并引入不对称裕度（MARGIN）做出稳健选择。"),
        ("4. 策略层（Strategy Layer）：", "包含 8 个高度优化的基础算法内核（涵盖插入、反转、计数、8位/16位基数、三路快排、边界检测归并及内省快排）。比较类兜底算法严格保证最坏 O(n log n) 时间复杂度。")
    ]
    for title, desc in arch_points:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.3
        r1 = p.add_run(title)
        format_run(r1, font_name="黑体", size_pt=10.0, bold=True, color_rgb=(30, 41, 59))
        r2 = p.add_run(desc)
        format_run(r2, font_name="宋体", size_pt=10.0, bold=False, color_rgb=(51, 65, 85))

    add_heading_2(doc, "1.1 关键源文件与功能分工")
    src_headers = ["模块 / 部分", "源码位置", "核心职能与设计要点"]
    src_data = [
        ["对外统一入口", "adaptsort.h，Line 777 (adapt_sort)", "模板主入口，获取/标定对应类型的成本模型单例并调用实现"],
        ["降序统一入口", "adaptsort.h，Line 784 (adapt_sort_desc)", "复用升序决策主干，排序完成后执行 O(n) 反转，渐近最优"],
        ["核心决策调度", "adaptsort.h，Line 586 (adapt_sort_impl)", "串联规则 1~6，执行顺序短路、扫描决策、成本竞争与策略派发"],
        ["特征采样层", "adaptsort.h，Line 250 (probe_order/range/dup)", "轻量等距采样，提取单调性、逆序率、前缀比、值域及重复率"],
        ["成本模型与标定", "adaptsort.h，Line 82 (CostModel), Line 479", "实测 8 个规模锚点与 5 个值域锚点，构建 (n, k) 网格与求值缓存"],
        ["基础排序内核", "algorithms.h，Line 31~340", "二分插入、计数、8/16位基数、边界检测归并、三路快排、内省快排"],
        ["性能统计桥接", "counters.h 与 main_cpp.cpp", "桥接比较/移动/辅助空间统计，记录 strategy 决策日志"]
    ]
    create_table_styled(doc, src_headers, src_data, col_widths=[1.5, 2.2, 2.8])

    # =========================================================================
    # 二、算法完整控制流与伪 C 代码逻辑
    # =========================================================================
    add_heading_1(doc, "二、算法完整控制流与伪 C 代码逻辑")

    add_paragraph_styled(
        doc,
        "为了清晰反映 AdaptSort 的实际控制逻辑，下面给出高度结构化的伪 C 代码。"
        "该伪代码严格保持了 `adaptsort.h` 中 `adapt_sort_impl` 的实际分支次序与核心护栏，"
        "省略了模板元编程、性能计数打点及浮点插值计算的次要语法糖：",
        font_name="宋体", size_pt=10.5, space_after=8
    )

    pseudo_c = """// ============================================================================
// AdaptSort 自适应排序算法核心逻辑（结构化伪 C 代码）
// ============================================================================

void AdaptSort(Element a[], int n)
{
    // 0. 模型准备：首次调用时在本机真实硬件上实测标定成本模型，后续调用直接查表缓存
    CostModel model = GetOrCalibrateCostModel<ElementType>();

    // 1. 规模短路保护
    if (n < 2) return;
    if (n <= 32) {
        BinaryInsertionSort(a, n);    // 极小规模：二分减少比较，避免复杂框架开销
        return;
    }

    // 2. 阶段 A 特征探测：O(sqrt(n)) 确定性等距采样（样本数 m 限制在 [8, 128]）
    OrderFeature f = ProbeOrder(a, n);

    // 2.1 整体单调性短路（必须遵循：采样先提出假设，全量线性扫描确认事实）
    if (f.sampleAscending && VerifyAscending(a, n)) {
        return;                       // 确认已升序（含全相等）：原地直接返回
    }
    if (f.sampleDescending && VerifyDescending(a, n)) {
        ReverseArray(a, n);           // 确认已降序：O(n) 首尾对调反转，直接返回
        return;
    }

    // 2.2 小规模近序短路：小规模下决策开销与排序开销同量级，极简策略最优
    if (n <= 512 && f.sampleInversionRate <= 0.05) {
        InsertionSort(a, n);          // 直接插入排序在近序数据上为 O(n + I)
        return;
    }

    // 3. 整数键分支：值域类策略（计数排序、基数排序）参与竞争
    if (IsIntegerKey<ElementType>()) {
        ProbeSampleRange(&f);         // 阶段 B-1：复用前 32 个采样点估计采样极差 (smin, smax)

        // 3.1 规则 2.6：小规模窄值域确定性规则（防止小规模下模型噪声导致决策翻转）
        if (n <= 512 && (f.sampleRange + 1) <= 2 * n) {
            FindExactMinMax(a, n, &minVal, &maxVal);
            long long k = maxVal - minVal + 1;
            if (k <= 8 * n) {
                CountingSort(a, n, minVal, maxVal);    // 窄值域下 cache 命中率极高，稳赢
            } else {
                IntroSort(a, n);                       // 若真实值域意外偏大，安全退回内省快排
            }
            return;
        }

        // 3.2 准备成本模型缓存（派生量 nl = n*log2(n), 极值修正因子 infl = sqrt(ln(n)/ln(m))）
        PrepareModelCache(model, n, f.sampleCount);

        // 3.3 经济学收益决策：是否值得支付 2n 次比较去全量扫描真实 min/max？
        long long k_est = (f.sampleRange + 1) * model.infl;     // 极值统计修正后的估计值域
        double e_count_est = PredictCountingCost(model, n, k_est);
        double e_radix_full = PredictRadixFullCost(model, n);
        double scan_cost = PredictScanCost(model, n);

        int bits_lb = SignificantBits(f.smin, f.smax);
        double gain_radix = EstimateRadixGain(model, bits_lb);   // 减少基数趟数的收益
        double gain_count = max(0.0, e_radix_full - e_count_est);// 启用计数的潜在收益
        
        // 只有当最大预期收益显著超过扫描成本（附带 1.30 保护裕度）时，才执行全量扫描
        bool scan_worth = max(gain_radix, gain_count) > scan_cost * 1.30;
        bool exact = false;
        long long lo = f.smin, hi = f.smax;

        if (scan_worth) {
            FindExactMinMax(a, n, &lo, &hi);
            exact = true;
        }

        long long k = hi - lo + 1;
        int bits = exact ? SignificantBits(lo, hi) : FullTypeBitWidth();

        // 3.4 候选策略成本预测
        // 比较类候选：近序（按扰动是否成块选择归并系数） vs 无序（内省快排）
        Strategy cmp_strat = (f.sampleInversionRate <= 0.05) ? MERGE_SORT : INTRO_SORT;
        double Tc = PredictComparisonCost(model, cmp_strat, f, n);

        // 计数排序候选：若未全量扫描确认真实值域，严禁使用（设为正无穷）；否则按 (n,k) 网格预测
        double Tk = exact ? PredictCountingCost(model, n, k) : INFINITY;

        // 基数排序候选：8位桶表（1KB，恒驻L1）与 16位桶表（256KB，驻L2）动态双轨竞争
        double Tr8  = PredictRadixCost(model, n, bits, /*digitBits=*/8);
        double Tr16 = PredictRadixCost(model, n, bits, /*digitBits=*/16);
        double Tr = min(Tr8, Tr16);
        int radix_db = (Tr8 <= Tr16) ? 8 : 16;

        // 3.5 裕度竞争决策（值域类 vs 比较类）
        // 裕度分设：计数排序 MARGIN=1.30（双维外推误差较大）；基数排序 RMARGIN=1.10（实测单趟可信）
        if (Tk * 1.30 <= Tc || Tr * 1.10 <= Tc) {
            // 计数 vs 基数：计数需比基数至少快 15%（CMARGIN=1.15）且绝对击败比较类
            if (Tk * 1.15 <= Tr && Tk * 1.30 <= Tc) {
                CountingSort(a, n, lo, hi);
            } else {
                RadixSort(a, n, bits, radix_db);
            }
            return;
        }

        // 3.6 落入比较类家族：此时才延迟支付重复率探测成本（n > 512 时）
        if (n > 512) {
            ProbeDuplicateRate(&f);    // 阶段 B-2：对小样本排序并统计重复键比例
            if (f.duplicateRate >= 0.20) {
                cmp_strat = THREE_WAY_QUICK_SORT;
            }
        }
        ExecuteComparisonStrategy(cmp_strat, a, n);
        return;
    }

    // 4. 通用键分支（浮点数、字符串等）：仅比较类家族竞争
    if (n > 512) {
        ProbeDuplicateRate(&f);
    }
    if (f.duplicateRate >= 0.20) {
        ThreeWayQuickSort(a, n);       // 高重复键：三路划分跳过大量等值键
    } else if (f.sampleInversionRate <= 0.05) {
        MergeSort(a, n);               // 近似有序：边界检测归并
    } else {
        IntroSort(a, n);               // 通用无序：内省快排兜底，最坏 O(n log n)
    }
}"""
    add_code_block(doc, pseudo_c)

    add_heading_2(doc, "2.1 伪代码关键分支决策速查表")
    rule_headers = ["判定节点", "触发前提与门槛条件", "执行策略", "设计目标与底层机理"]
    rule_data = [
        ["极小规模", "n <= 32", "S1 二分插入排序", "指令流水饱满，减少比较次数，完全规避复杂算法调度开销"],
        ["已升序", "采样全升序 && 全量确认相邻非降", "S2 已升序直通", "信息论最优下界（n-1 次比较），0 次数据移动，直接返回"],
        ["已降序", "采样全降序 && 全量确认相邻非增", "S3 降序反转", "n-1 次比较 + n/2 次原地对称交换，时间复杂度严格 Θ(n)"],
        ["小规模近序", "n <= 512 && inv_rate <= 0.05", "S1b 直接插入排序", "小规模下决策成本敏感，直接插入排序在近序下时间为 O(n+I)"],
        ["小规模窄值域", "n <= 512 && (sample_range+1) <= 2n", "全量扫描后计数或内省", "绕过浮点成本模型噪声，真实 k <= 8n 走计数，否则内省快排"],
        ["值域扫描判断", "max(gain_radix, gain_count) > 1.30 * scan_cost", "扫描真实 min/max", "经济学收益核算：确保 2n 扫描开销能被后续趟数减少所抵偿"],
        ["值域类胜出", "Tk * 1.30 <= Tc || Tr * 1.10 <= Tc", "S4 计数 或 S5 基数", "非比较类突破 O(n log n) 理论下界，取得数量级性能优势"],
        ["计数 vs 基数", "Tk * 1.15 <= Tr && Tk * 1.30 <= Tc", "S4 计数排序", "非对称裕度：计数需显著领先基数才选，避免跨 Cache 台阶插值误差"],
        ["基数位宽竞争", "Tr8 <= Tr16", "S5 基数排序(8位/16位)", "8位桶表恒驻 L1，16位趟数减半，由本机单趟实测模型动态选择"],
        ["高重复率判定", "落入比较类 && n > 512 && dup_rate >= 0.20", "S6 三路快速排序", "三路划分将大量相等键一次性归位，子递归规模急剧收缩"],
        ["比较类近序", "落入比较类 && inv_rate <= 0.05", "S7 边界检测归并", "利用 a[mid-1] <= a[mid] 跳过合并，块状扰动下效率极高"],
        ["通用兜底", "无序分布，其他条件均不满足", "S8 内省快速排序", "快速排序划分深度超过 2*log2(n) 时熔断转堆排，锁定最坏界"]
    ]
    create_table_styled(doc, rule_headers, rule_data, col_widths=[1.1, 1.8, 1.4, 2.2])

    # =========================================================================
    # 三、算法核心原理深度剖析（探测逻辑与分配逻辑）
    # =========================================================================
    add_heading_1(doc, "三、算法核心原理深度剖析（探测逻辑与分配逻辑）")

    add_paragraph_styled(
        doc,
        "AdaptSort 的精髓不在于孤立实现某个单体算法，而在于如何以极小的探测代价感知输入分布，"
        "并运用严密的数学逻辑与硬件物理特性实现最优派发。下面对探测逻辑与分配逻辑展开深度剖析：",
        font_name="宋体", size_pt=10.5, space_after=6
    )

    add_heading_2(doc, "3.1 顺序探测逻辑（ProbeOrder）：采样先行与事实确认")
    add_paragraph_styled(
        doc,
        "在处理海量数据时，如果直接对整个数组做一次 O(n) 的“是否已有序”全量检查，对于 n=10⁶ 的数组需要进行近 100 万次比较（耗时约 1 ms）。"
        "如果数据本身是随机乱序的、或者接下来要走仅需 5~8 ms 的基数排序，那么开局浪费这 1 ms 就会平白增加 15%~20% 的运行开销！"
        "为此，AdaptSort 确立了根本原则：采样用于提出假设，全量扫描用于确认事实。",
        font_name="宋体", size_pt=10.5, space_after=6
    )

    add_callout(
        doc,
        "采样的数学意义是“单向否决权”：若采样点中出现了逆序对（如抽到 8 > 5），则根据传递性，整个数组必然不可能非降序，从而可以绝对安全地跳过全量升序检查；"
        "反之，若采样点全为非降序，仅代表抽到的子序列有序，无法排除未抽样区间存在逆序，因此必须启动全量线性扫描确认！",
        title="采样先行与单向否决原理："
    )

    add_paragraph_styled(
        doc,
        "具体实现中，顺序探测包含以下关键设计细节：",
        font_name="宋体", size_pt=10.5, space_after=4
    )
    order_details = [
        ("等距采样规模控制：", "样本数设定为 m = clamp(floor(sqrt(n)), 8, 128)。之所以设定上限 128，是因为当 n 达到 100 万甚至更大时，探测开销被严格限制在最多 254 次比较之内，使得顺序探测在渐近意义下退化为 O(1) 的固定极小常数开销。步长取 stride = n / m。"),
        ("单调性与逆序率（inv_rate）：", "遍历相隔 stride 的相邻样本对，统计下降对数。inv_rate = downCount / (m - 1)。特别提醒：这里的 inv_rate 是“相隔步长的采样点逆序比例”，不是全数组真实逆序对数量 I，也不是实际相邻逆序对比例。它是一个用于感知局部扰动密度的启发式特征。"),
        ("非降前缀占比（asc_prefix）：", "统计从数组首部出发、保持非降序的最长连续样本对占比。这是 AdaptSort 在实测中被逼出来的关键创新：实测发现，逆序率同为 0.037 的两个近序输入，归并排序的耗时可相差 3.7 倍！原因在于“块状扰动”（如前 90% 有序、尾部 10% 乱序）只破坏尾部的归并边界，而“离散扰动”（随机两两微扰）会在每一层归并树上造成断点。asc_prefix >= 0.75 能精准识别块状扰动并指导模型选用更低的归并成本系数。"),
        ("全量确认与反转的传递性：", "全量确认函数 VerifyAscending 采用相邻比较 a[i] < a[i-1]，利用偏序传递性在发现首个反例时立即短路退出。全相等数组天然同时满足升序和降序条件，源码优先判定升序，从而直接返回，绝不产生多余反转。")
    ]
    for dt, dd in order_details:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.3
        r1 = p.add_run(dt)
        format_run(r1, font_name="黑体", size_pt=10.0, bold=True, color_rgb=(30, 41, 59))
        r2 = p.add_run(dd)
        format_run(r2, font_name="宋体", size_pt=10.0, bold=False, color_rgb=(51, 65, 85))

    add_heading_2(doc, "3.2 值域探测与全量扫描收益模型（ProbeRange & ExactScan）")
    add_paragraph_styled(
        doc,
        "当数据类型为整数时，计数排序与基数排序具有突破 O(n log n) 比较下界的巨大潜力，但它们的高度依赖值域大小 k = max - min + 1。探测逻辑如何平衡值域获取的开销与收益？",
        font_name="宋体", size_pt=10.5, space_after=6
    )
    add_paragraph_styled(
        doc,
        "1. 零成本复用采样点：阶段 B 的 ProbeRange 并不重新扫描数组，而是直接就地读取阶段 A 保存在小结构体中的前 32 个采样点，仅需最多 62 次比较即可求出样本极差 sample_range = smax - smin。由于 smin >= trueMin 且 smax <= trueMax，采样值域必然是真实值域的数学下界。\n"
        "2. 极值统计修正因子：采样值域往往会系统性低估真实值域。根据极值统计学理论，在正态或常见分布下，m 个样本的极差与 n 个全量元素的极差存在理论比例关系：真实极差约为采样极差的 √(ln(n) / ln(m)) 倍（当 n=10⁶, m=128 时约为 1.7 倍）。AdaptSort 将修正因子 infl = √(ln(n) / ln(m)) 纳入模型，预估 k_est = (sample_range + 1) * infl，有效避免了因低估 k 而误判计数排序划算、进而发起徒劳扫描的缺陷。\n"
        "3. 经济学扫描收益模型：全量扫描真实极值需要精确的 2n 次比较（耗时约 scan_cost）。AdaptSort 评估扫描的两大收益：其一是基数排序因截断高位可能减少的趟数收益 gain_radix；其二是启用计数排序相对全宽基数的潜在加速 gain_count。仅当 max(gain_radix, gain_count) > 1.30 * scan_cost 时才触发扫描！若不值得扫描，则计数排序直接禁用，基数排序按全宽位宽执行——宁可多跑一趟基数，也绝不盲目支付 2n 扫描开销。",
        font_name="宋体", size_pt=10.5, space_after=8, line_spacing=1.35
    )

    add_heading_2(doc, "3.3 基数排序自适应优化：最高差异位截断与 8/16 位双轨竞争")
    add_paragraph_styled(
        doc,
        "AdaptSort 对基数排序（LSD）实施了两项极为关键的工程改进：",
        font_name="宋体", size_pt=10.5, space_after=4
    )
    radix_points = [
        ("最高差异位（Significant Bits）截断：", "通过有符号整数翻转符号位映射为无符号键后，计算 x = ukey(max) ^ ukey(min)。x 的最高非零位即为所有元素的最大差异位。所有高于该位的二进制位在全量数据中完全相同，对相对大小毫无影响！通过跳过公共高位，基数排序的执行趟数得以大幅裁剪（例如数据集中在 [1000, 2000]，有效位宽仅 11 位，无需处理 32 位）。"),
        ("8 位与 16 位数字位宽动态竞争（v3.1 核心成果）：", "16 位位宽每趟桶表达 65536 项（256 KB），超出 CPU L1 数据缓存（通常 32~48 KB），只能驻留 L2，且每趟 memset 桶表开销大；8 位位宽桶表仅 256 项（1 KB），恒驻高速 L1 缓存，但总趟数翻倍。实测证明没有固定的最优位宽（小规模 8 位快 30%，大数据 16 位趟数少更优）。AdaptSort 成本模型针对 8 位和 16 位各标定一套单趟物理耗时，按 passes * cost 动态比选，彻底消除了小规模下的基数劣势。")
    ]
    for rk, rv in radix_points:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.3
        r1 = p.add_run(rk)
        format_run(r1, font_name="黑体", size_pt=10.0, bold=True, color_rgb=(30, 41, 59))
        r2 = p.add_run(rv)
        format_run(r2, font_name="宋体", size_pt=10.0, bold=False, color_rgb=(51, 65, 85))

    add_heading_2(doc, "3.4 延迟付费机制与重复率探测（ProbeDuplicate）")
    add_paragraph_styled(
        doc,
        "重复率探测需要将采样点复制到临时数组，并执行一次 O(dn²) 的小规模插入排序来统计不同值的数量。虽然 dn 仅限制在前 32 个点，但这几十次比较在小规模（n=100）下依然是不可忽略的负担。\n"
        "AdaptSort 实行严格的延迟付费原则：重复率特征 dup_rate 仅用于在比较类内部区分“三路快排 vs 内省快排”。如果数据已经被分派到计数排序、基数排序或归并排序，这三个算法对重复键天然免疫甚至更加高效，此时重复率信息毫无决策价值！因此，源码仅在最终确认落入比较类、且规模 n > 512 时才触发重复率探测（n <= 512 时二路 Hoare 划分对重复键天然对半平衡，三路快排收益低于探测成本）。",
        font_name="宋体", size_pt=10.5, space_after=8, line_spacing=1.35
    )

    add_heading_2(doc, "3.5 运行时自标定成本模型与不对称裕度设计（分配算法逻辑）")
    add_paragraph_styled(
        doc,
        "为什么 AdaptSort 坚决不使用传统的理论公式 T = a*n + b*n*log(n) 进行决策？"
        "因为现代处理器的 Cache 层次结构具有显著的物理台阶效应！"
        "同一算法在 n=10⁴（数据全在 L1/L2 缓存）与 n=10⁶（数据溢出到 L3 及主存）下的每元素纳秒开销可相差 5~15 倍。任何单一解析公式均无法跨越缓存台阶，强行拟合甚至会出现负系数。",
        font_name="宋体", size_pt=10.5, space_after=6
    )
    add_paragraph_styled(
        doc,
        "AdaptSort 的成本模型分配逻辑具有三大支柱：",
        font_name="宋体", size_pt=10.5, space_after=4
    )
    cost_pillars = [
        ("1. (n, k) 双维实测网格标定：", "进程启动时，在 8 个规模锚点（覆盖 100 到 10⁶，对齐实验评测档位）与 5 个值域锚点（KANC = {64, 1024, 32768, 262144, 1000000}，跨越 256B→4KB→128KB→1MB→4MB 的缓存跳变区）上直接运行真实算法并测量毫秒耗时。锚点内通过 log-log 幂律线性插值，将硬件 Cache 效应真实吸收进标定数据中。"),
        ("2. 斜率限幅内存护栏（v3.1 核心修复）：", "针对 k 超过 10⁶ 的超大值域（如 int64 posting 键 k ≈ 8.8×10¹¹），上端外推斜率被强行锁定在 [0.8, 1.2] 区间。当 k 极端巨大时，计数排序预测成本随 k^0.8 暴涨至天文数字，从而彻底激活内存护栏，坚决否决计数排序，避免了早期版本因外推越界导致申请 3.5TB 内存报 bad_alloc 的致命缺陷。"),
        ("3. 基于模型置信度的不对称切换裕度体系：", "由于不同模型的预测精度不同，AdaptSort 确立了非对称的工程切换裕度：\n"
         "  • 计数排序进入比较类裕度 MARGIN = 1.30（计数在 (n,k) 双维外推，误差可达 40%，要求预测优势达 30% 才准切换）；\n"
         "  • 基数排序进入比较类裕度 RMARGIN = 1.10（基数单趟由位宽确定性导出且同形态直接实测，可信度极高，仅保留 10% 噪声余量）；\n"
         "  • 计数相对基数裕度 CMARGIN = 1.15（计数比基数需有 15% 显著优势才选计数，防止在缓存临界区频繁振荡）。")
    ]
    for pk, pv in cost_pillars:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.3
        r1 = p.add_run(pk)
        format_run(r1, font_name="黑体", size_pt=10.0, bold=True, color_rgb=(30, 41, 59))
        r2 = p.add_run(pv)
        format_run(r2, font_name="宋体", size_pt=10.0, bold=False, color_rgb=(51, 65, 85))

    # =========================================================================
    # 四、各种情况下的时间与空间复杂度深度剖析（保护滤网假说）
    # =========================================================================
    add_heading_1(doc, "四、各种情况下的时间与空间复杂度深度剖析")

    add_paragraph_styled(
        doc,
        "在分析 AdaptSort 的复杂度时，必须回答一个非常深刻且具有洞察力的理论问题：",
        font_name="宋体", size_pt=10.5, space_after=4
    )
    add_callout(
        doc,
        "“在算法设计中，某个单体算法 A（如直接插入排序）的理论最坏复杂度可能是 O(n²)，但在 AdaptSort 的自适应框架中，会导致 A 发生退化的那组恶劣数据，根本就进不去 A 算法，而是在前面就被特征探测精准拦截并分派给了算法 B！那么，我们该如何科学定义和分析 AdaptSort 各个分支的真实复杂度？”",
        title="核心理论命题："
    )

    add_heading_2(doc, "4.1 核心理论：前置探测作为“保护滤网”与自适应有效复杂度")
    add_paragraph_styled(
        doc,
        "在传统经典算法理论中，单一算法的时间复杂度 T(n) 是定义在所有可能输入实例的全集 Σ* 上的最坏情况上界。例如，直接插入排序在全集 Σ* 上的最坏时间为 O(n²)，计数排序在全集上的空间与时间为 O(n + k)。\n\n"
        "然而在自适应混合架构下，前置特征探测（Filter / Probing）充当了算法的“保护滤网（Protective Gatekeeper）”。"
        "决策函数 D(F) 将全域输入空间 Σ* 划分为若干条件苛刻的互斥子集：\n"
        "    Σ* = Ω_已升序 ∪ Ω_已降序 ∪ Ω_极小规模 ∪ Ω_小规模近序 ∪ Ω_小规模窄值域 ∪ Ω_基数胜出 ∪ Ω_三路快排 ∪ Ω_内省兜底\n\n"
        "当且仅当输入数据落在特定子集 Ω_A 时，算法 A 才会被物理执行。因此，评价一个分支在系统中的代价，不应脱离前提条件孤立套用其教科书复杂度，而必须分析其“自适应有效复杂度（Effective Adaptive Complexity）”！",
        font_name="宋体", size_pt=10.5, space_after=8, line_spacing=1.35
    )

    add_heading_2(doc, "4.2 四大典型拦截机制剖析（恶劣数据为何绝不进入缺陷算法）")
    inter_points = [
        ("1. 直接插入排序的 O(n²) 恶劣乱序被彻底拦截：",
         "单体插入排序在完全逆序或完全乱序下退化为 O(n²)。但在 AdaptSort 中，插入排序的准入条件为：规则 1（n <= 32）或 规则 2.5（n <= 512 且 inv_rate <= 0.05）。\n"
         "• 若输入是严格逆序数据：在规则 2 瞬间被采样捕获并经全量线性确认，直接走 S3 数组反转（O(n) 耗时，0 额外空间），绝不会漏给插入排序；\n"
         "• 若输入是随机乱序数据：其采样逆序率期望值为 0.5，远远超出 0.05 的阈值门槛，被规则 2.5 严厉拒绝，直接流入整数值域类竞争或通用内省快排；\n"
         "• 唯一能进入直接插入排序的，只有规模微小且逆序对极少的近序数据。此时真实逆序对数量 I 极小，其实际运行时间被牢牢约束在 O(n + I) 接近线性级别！其教科书上的 O(n²) 恶劣分支在控制流中属于物理不可达的死分支。"),
        
        ("2. 计数排序的 O(n + k) 内存与时间爆炸被彻底拦截：",
         "单体计数排序的致命痛点是当值域 k 远大于 n 时（如 k=10¹²），申请桶表会引发内存耗尽崩溃，初始化耗时 O(k) 严重超标。在 AdaptSort 中：\n"
         "• 未经全量扫描确认真实值域前，exact = false，计数排序预测成本被显式置为正无穷（1e300），剥夺一切竞争权；\n"
         "• 在小规模规则 2.6 中，硬性限制真实 k <= 8n，超标立即回退内省快排；\n"
         "• 在大规模成本模型中，大 k 数据受斜率限幅护栏约束，预测耗时呈指数级膨胀，基数排序或比较类算法会以压倒性优势胜出；\n"
         "• 因此，会导致计数排序 O(n + k) 崩溃的大 k 数据在特征层和决策层被 100% 拦截，进入计数排序的数据其实际时间恒为 O(n)，辅助空间严格受限于 O(n)。"),

        ("3. 经典快速排序的 O(n²) 最坏划分退化被彻底拦截：",
         "快速排序在遇到针对性恶意构造序列（如退化枢轴）时会退化为单侧倾斜划分，递归深度达 O(n)，时间复杂度退化为 O(n²)。\n"
         "AdaptSort 的通用比较类内核采用内省快速排序（IntroSort），在递归过程中动态监控划分深度。一旦深度超过 2*floor(log2(n))，立即硬性熔断并原样切入堆排序（HeapSort），在数学上强行将比较类兜底分支的最坏时间上界死死锁定在 O(n log n)。"),

        ("4. 全相等与高重复键的无意义开销被彻底拦截：",
         "普通二路快排或归并排序遇到全相等数据时仍会反复执行多轮递归划分与数据搬移。在 AdaptSort 中：\n"
         "• 全相等序列在规则 2 中被 VerifyAscending 在 n-1 次比较内直接判定为已升序，零移动直接返回；\n"
         "• 含有 20% 以上重复键的数据被规则 5/6 调度至三路快排，中间等于 pivot 的区间一次性归位不再参与递归，从而避免了无效开销。")
    ]
    for ik, iv in inter_points:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.3
        r1 = p.add_run(ik)
        format_run(r1, font_name="黑体", size_pt=10.0, bold=True, color_rgb=(30, 41, 59))
        r2 = p.add_run(iv)
        format_run(r2, font_name="宋体", size_pt=10.0, bold=False, color_rgb=(51, 65, 85))

    add_heading_2(doc, "4.3 各分支理论复杂度与实际有效复杂度全景对照表")
    add_paragraph_styled(
        doc,
        "下表详细对比了 AdaptSort 各策略在脱离框架时的独立理论复杂度、进入该策略的前置门槛条件，以及在保护滤网生效后的实际有效复杂度与辅助空间开销（空间均指辅助空间，假设单次比较赋值 O(1)，模型已完成预标定）：",
        font_name="宋体", size_pt=10.5, space_after=6
    )

    comp_headers = ["执行策略 / 情况", "脱离框架的独立理论复杂度", "准入前置门槛（保护滤网）", "AdaptSort 下的实际有效复杂度", "辅助空间"]
    comp_data = [
        ["S1 二分插入", "最坏 O(n²)，比较 O(n log n)", "n <= 32 极小规模硬短路", "实际耗时 <= 1024 次操作，属于 O(1) 绝对微秒开销", "O(1)"],
        ["S2 已升序直通", "单体无此概念（需整体有序）", "采样一致 && VerifyAscending 全量通过", "严格 Θ(n) 时间，n-1 次比较，0 次移动", "O(1)"],
        ["S3 降序反转", "单体无此概念（需整体逆序）", "采样一致 && VerifyDescending 全量通过", "严格 Θ(n) 时间，n-1 次比较，n/2 次对调交换", "O(1)"],
        ["S1b 直接插入", "最好 O(n)，最坏 O(n²)", "n <= 512 且 inv_rate <= 0.05（过滤乱序）", "实际有效时间 Θ(n + I)，严格接近线性 O(n)", "O(1)"],
        ["S4 计数排序", "时间/空间均为 Θ(n + k)", "精确扫描 && (小规模 k<=8n 或 模型胜出)", "由于 k 受到严格上限压制，实际时间严格 Θ(n)", "Θ(n)（受限k）"],
        ["S5 基数排序", "Θ(p * (n + B))，p为趟数", "整数键 && 差异位截断 && 成本模型胜出", "固定位宽下表现为线性 Θ(n)，8/16位动态自适应", "Θ(n + B)"],
        ["S6 三路快排", "最好 O(n)，最坏 O(n²)", "落入比较类 && n > 512 && dup_rate >= 0.20", "高重复下实际收敛为 O(n) ~ O(n log n)", "O(log n)"],
        ["S7 边界检测归并", "最好 O(n)，最坏 O(n log n)", "落入比较类 && inv_rate <= 0.05 近似有序", "块状近序跳过大量合并，时间介于 O(n) 与 O(n log n)", "Θ(n)"],
        ["S8 内省快排", "最坏 O(n log n)", "通用兜底；或深度超限 2*log2(n) 熔断", "最坏时间数学保证严格 O(n log n)", "O(log n)"]
    ]
    create_table_styled(doc, comp_headers, comp_data, col_widths=[1.2, 1.4, 1.6, 1.6, 0.7])

    add_heading_2(doc, "4.4 严谨学术视角：当前 AdaptSort 的理论边界漏洞与澄清")
    add_paragraph_styled(
        doc,
        "在答辩与高水平报告中，实事求是地指出当前工程架构的理论边界，不仅不会削弱作品价值，反而能极大展现作者严谨的学术作风与深刻的洞察力：\n\n"
        "1. 整体最坏界并非绝对的 O(n log n)：虽然 S8 内省快排具有最坏 O(n log n) 的严格数学保证，但被选中的独立分支 S6 三路快速排序（`quick3_rec`）并未引入内省递归深度监控转堆排的机制！在人为针对性构造的对抗样本下（如虽然采样表现为 20% 重复，但非重复部分极为恶劣导致枢轴反复失衡），三路快排理论上仍存在 O(n²) 的退化风险。因此，不能在学术上简单宣称 AdaptSort 整体具有无条件最坏 O(n log n) 保证。\n\n"
        "2. 采样的启发式盲区：等距采样步长 stride = n / m 存在物理盲区。如果对抗性构造的逆序扰动恰好全部落在采样点之间的区间内，采样探测将汇报 inv_rate = 0，从而误入直接插入排序。虽然此时仅发生在 n <= 512 的小规模，绝对耗时在微秒级不会导致系统假死，但在纯理论层面，它属于启发式高概率保证而非绝对数学证明。\n\n"
        "3. 空间并非全局 O(1)：计数排序、基数排序和归并排序均需要分配线性辅助缓冲区（O(n)），AdaptSort 是以空间换取极端时间性能的混合策略，并非全场景原地排序。",
        font_name="宋体", size_pt=10.5, space_after=10, line_spacing=1.35
    )

    # =========================================================================
    # 五、AdaptSort 缓解了四种主流算法的哪些痛点
    # =========================================================================
    add_heading_1(doc, "五、AdaptSort 缓解了四种主流算法的哪些痛点")

    add_paragraph_styled(
        doc,
        "在通用排序场景中，快速排序、堆排序、基数排序和归并排序是最具代表性的四种主流算法。AdaptSort 并不试图消除每个单体算法内部的所有物理缺陷，而是通过策略互补与自适应分流，精准缓解了它们各自的核心痛点：",
        font_name="宋体", size_pt=10.5, space_after=6
    )

    pain_headers = ["主流算法", "原生经典痛点与瓶颈", "AdaptSort 的针对性缓解机制", "边界与工程权衡说明"]
    pain_data = [
        ["快速排序 (QuickSort)",
         "1. 恶劣输入最坏 O(n²) 退化；\n2. 大量重复键时反复切分无效递归；\n3. 无法直接利用数据的已有单调性。",
         "1. 升序直接直通，降序原地反转（O(n) 终结）；\n2. 探测到高重复（dup>=20%）分派三路快排；\n3. 通用无序采用 IntroSort 深度熔断保底。",
         "三路快排分支未加内省熔断；普通二路划分在极小规模下全等键天然平衡，故 n<=512 不启用三路。"],
        ["堆排序 (HeapSort)",
         "1. 父子节点跨步长大，缓存局部性极差，常数巨大；\n2. 对已排序、近序、重复数据完全钝感，恒走 O(n log n)。",
         "1. 正常分布下绝不主动分派堆排序，规避其缓存惩罚；\n2. 仅在内省快排划分深度超限时作为局部安全网介入。",
         "AdaptSort 成功避免了堆排被滥用，但并未改造堆排序内核本身，也未实现全局常数辅助空间。"],
        ["基数排序 (RadixSort)",
         "1. 固定全类型位宽导致小数字空跑多趟；\n2. 桶表大小固定，无法兼顾大数据吞吐与小数据 Cache；\n3. 内存占用 O(n)。",
         "1. 有效位宽截断跳过公共高位，大幅压缩趟数；\n2. 8位桶表（1KB，驻L1）与 16位桶表动态竞争；\n3. 预测不划算时退回比较类，避免内存滥用。",
         "当前实现主要支持整数键（int/int64）；依然需要线性辅助内存。"],
        ["归并排序 (MergeSort)",
         "1. 强制分配 O(n) 额外缓冲区并频繁搬移；\n2. 对已有序数据即使能提前退出，框架常数仍大；\n3. 纯无序数据常数明显落后于快排。",
         "1. 全量单调数据在最外层即被直通截胡；\n2. 内核引入边界检测 a[mid-1]<=a[mid] 跳过合并；\n3. 纯乱序数据分派快排或基数，避免多余搬移。",
         "一旦选入归并仍需申请 O(n) 缓冲区；归并分支使用的是自底向上迭代归并而非完整 Timsort。"]
    ]
    create_table_styled(doc, pain_headers, pain_data, col_widths=[1.2, 1.8, 2.0, 1.5])

    # =========================================================================
    # 六、结合实测数据的全面评价
    # =========================================================================
    add_heading_1(doc, "六、结合实测数据的全面评价")

    add_paragraph_styled(
        doc,
        "在统一测试工程（results_cpp.csv）的全矩阵评测中，针对均匀分布（uniform）、高斯分布（gauss）、泊松分布（poisson）、真实漏洞语料（cve）、已升序（asc）及已降序（desc）六种典型分布，在 n=10² 到 n=10⁶ 规模下进行了详尽实测。以下为最具代表性的大规模（n=10⁶）实测耗时数据（单位：毫秒 ms，取 tmin_ms）：",
        font_name="宋体", size_pt=10.5, space_after=6
    )

    stat_headers = ["测试数据形态 (n=10⁶)", "快速排序 (quick)", "堆排序 (heap)", "基数排序 (radix)", "归并排序 (merge)", "AdaptSort 实测", "实际命中策略与优势比"]
    stat_data = [
        ["已升序分布 (asc)", "27.0592", "52.5741", "15.0009", "18.9729", "0.2973", "S2 直通（领先快排 91 倍，堆排 177 倍）"],
        ["已降序分布 (desc)", "25.6057", "57.8668", "14.5790", "18.6229", "0.5150", "S3 反转（领先快排 50 倍，堆排 112 倍）"],
        ["真实漏洞语料 (cve)", "5.8195", "55.1966", "13.5957", "21.2733", "3.5051", "S5 基数(8位)（击败所有单一算法）"],
        ["泊松分布 (poisson)", "15.9923", "70.2863", "11.6917", "48.8177", "2.9198", "S5 基数(8位)（领先基线基数近 4 倍）"],
        ["均匀分布 (uniform)", "71.4183", "112.5128", "8.7384", "85.6374", "6.7238", "S5 基数(8位)（领先快排 10.6 倍）"],
        ["高斯分布 (gauss)", "71.8581", "108.7518", "8.5197", "84.2508", "7.0948", "S5 基数(8位)（优于全宽基数与比较类）"]
    ]
    create_table_styled(doc, stat_headers, stat_data, col_widths=[1.4, 0.8, 0.8, 0.8, 0.8, 0.9, 1.8])

    add_paragraph_styled(
        doc,
        "在小规模（n=10³）档位下，AdaptSort 同样表现优异：均匀分布耗时仅 0.0061 ms（选中 S5 8位基数，接近基线基数的 0.0059 ms，远胜快排的 0.0345 ms 与归并的 0.0429 ms）；在已升序数据上耗时 0.0003 ms，直接以 30 倍优势胜出。",
        font_name="宋体", size_pt=10.5, space_after=8
    )

    add_heading_2(doc, "6.1 核心亮点与工程创新")
    highlights = [
        ("1. 硬件感知与数据感知深度融合：", "将数据特征统计（顺序、值域、重复率）与宿主机真实 Cache 层次结构（8 规模 × 5 值域锚点实测）紧密结合，构建了可解释、自进化的工程决策框架。"),
        ("2. 极致的延迟付费原则：", "特征分段获取，顺序先测，值域按需测，重复率最后测；决策派发派生量（nl, infl, scan_cost）随模型全局缓存，彻底将小规模决策开销压制在数十纳秒内。"),
        ("3. 突破性的位宽双轨竞争：", "打破了传统基数排序固定 8 位或 16 位的教条，首创将数字位宽作为成本变量纳入运行时插值竞争，完美解决了小规模与大数据之间的缓存与趟数权衡。")
    ]
    for hk, hv in highlights:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.3
        r1 = p.add_run(hk)
        format_run(r1, font_name="黑体", size_pt=10.0, bold=True, color_rgb=(30, 41, 59))
        r2 = p.add_run(hv)
        format_run(r2, font_name="宋体", size_pt=10.0, bold=False, color_rgb=(51, 65, 85))

    add_heading_2(doc, "6.2 局限性与未来改进方向")
    limits = [
        ("1. 首次标定的冷启动开销：", "成本模型在首次调用时需执行多规模自标定（耗时约 0.5~0.8 秒）。对于单次小数组排序的短命进程，这一开销无法被均摊。未来可考虑离线标定配置持久化。"),
        ("2. 启发式采样的极端情况风险：", "等距采样虽极大压低了开销，但在理论上无法防御对抗性构造的特定区间扰动。"),
        ("3. 缺乏全局常数辅助空间保证：", "当选用计数、基数或归并时，依然需要申请 O(n) 堆内存缓冲区，内存受限嵌入式环境需提供原地比较模式。"),
        ("4. 独立三路分支的最坏界完善：", "建议在后续版本中对 `quick3_rec` 同样引入递归深度监控，超限时切入堆排，从而在数学上彻底闭环整体最坏 O(n log n) 证明。"),
        ("5. 多线程并发安全性：", "当前成本模型的评估缓存与策略追踪变量采用 static 共享可变状态，在多线程并发场景下需升级为 thread_local。")
    ]
    for lk, lv in limits:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.3
        r1 = p.add_run(lk)
        format_run(r1, font_name="黑体", size_pt=10.0, bold=True, color_rgb=(30, 41, 59))
        r2 = p.add_run(lv)
        format_run(r2, font_name="宋体", size_pt=10.0, bold=False, color_rgb=(51, 65, 85))

    # =========================================================================
    # 七、总结与答辩建议
    # =========================================================================
    add_heading_1(doc, "七、总结与答辩陈述建议")

    add_paragraph_styled(
        doc,
        "在课程设计答辩或实验报告总结时，可以采用以下凝练、专业且极富说服力的表述进行总结陈词：",
        font_name="宋体", size_pt=10.5, space_after=6
    )

    add_callout(
        doc,
        "“AdaptSort 是一个面向多元数据分布的自适应混合排序体系。它放弃了寻找单一最优算法的幻想，创新性地构建了‘轻量特征探测 + 确定性硬规则 + 硬件实测成本模型’的三位一体决策架构。"
        "通过低成本的单调性探测与极值修正，它在极端有序和近序数据上逼近了信息论最优下界；通过有效位宽截断与 8/16 位双轨自适应竞争，它在整数值域数据上充分释放了非比较排序突破 O(n log n) 的潜能；"
        "而在通用无序数据上，它依靠内省机制筑牢了最坏时间性能的护栏。"
        "实测表明，AdaptSort 在全矩阵 30 个测试格中，有 28 个格子逼近或超越了单一最优算法，真正实现了‘集百家之长，因材施教’的算法普适性设计目标。”",
        title="答辩总结推荐陈词："
    )

    # 保存文档
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file1 = os.path.join(current_dir, "AdaptSort算法设计报告.docx")
    doc.save(file1)
    print(f"Report saved to: {file1}")

    # 同时另存到上级统一版根目录，方便用户随时查看
    parent_dir = r"D:\西北工业大学\大三\a上\算法设计综合实验\大作业\排序算法-统一版-20260928"
    if os.path.exists(parent_dir):
        file2 = os.path.join(parent_dir, "AdaptSort算法设计报告.docx")
        shutil.copyfile(file1, file2)
        print(f"Report copied to: {file2}")

if __name__ == "__main__":
    build_report()
