const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Footer, PageNumber, AlignmentType, HeadingLevel, WidthType, BorderStyle,
  ShadingType, PageOrientation, SectionType, NumberFormat, TableOfContents, PageBreak, TableLayoutType
} = require('docx');
const fs = require('fs');
const path = require('path');

const OUT = path.join(__dirname, 'AdaptSort核心调度算法设计逻辑与六类应用场景分析.docx');
const C = {
  primary: '0A1628', body: '000000', secondary: '5A6573', accent: '3B6F92',
  pale: 'EAF1F6', rule: 'BCCAD4', white: 'FFFFFF'
};
const fontBody = { ascii: 'Times New Roman', eastAsia: 'SimSun' };
const fontHead = { ascii: 'Times New Roman', eastAsia: 'SimHei' };
const twip = { top: 1440, bottom: 1440, left: 1701, right: 1417 };

function run(text, options = {}) {
  return new TextRun({ text, font: fontBody, size: 24, color: C.body, ...options });
}
function body(text, options = {}) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { firstLine: 480 },
    spacing: { before: 0, after: 100, line: 312 },
    children: [run(text)], ...options
  });
}
function heading(text, level = HeadingLevel.HEADING_1) {
  const size = level === HeadingLevel.HEADING_1 ? 32 : 28;
  return new Paragraph({
    heading: level,
    keepNext: true,
    spacing: { before: level === HeadingLevel.HEADING_1 ? 300 : 220, after: 120, line: 360 },
    children: [new TextRun({ text, font: fontHead, size, bold: true, color: C.primary })]
  });
}
function caption(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    keepNext: true,
    spacing: { before: 80, after: 80, line: 312 },
    children: [new TextRun({ text, font: fontBody, size: 21, color: C.secondary })]
  });
}
const noBorder = { style: BorderStyle.NONE, size: 0, color: C.white };
const tableBorders = {
  top: { style: BorderStyle.SINGLE, size: 6, color: C.accent },
  bottom: { style: BorderStyle.SINGLE, size: 6, color: C.accent },
  left: noBorder, right: noBorder,
  insideHorizontal: { style: BorderStyle.SINGLE, size: 2, color: C.rule },
  insideVertical: noBorder
};
const coverNoBorders = {
  top: noBorder, bottom: noBorder, left: noBorder, right: noBorder,
  insideHorizontal: noBorder, insideVertical: noBorder
};
function makeTable(headers, rows, widths) {
  const mkCell = (text, i, header = false) => new TableCell({
    width: { size: widths[i], type: WidthType.PERCENTAGE },
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    shading: header ? { type: ShadingType.CLEAR, fill: C.accent } : undefined,
    borders: {
      top: noBorder, bottom: noBorder, left: noBorder, right: noBorder
    },
    children: [new Paragraph({
      alignment: AlignmentType.LEFT,
      spacing: { before: 0, after: 0, line: 312 },
      children: [new TextRun({
        text, font: fontBody, size: header ? 21 : 20,
        bold: header, color: header ? C.white : C.body
      })]
    })]
  });
  const rowObjs = [new TableRow({
    tableHeader: true, cantSplit: true,
    children: headers.map((h, i) => mkCell(h, i, true))
  })];
  rows.forEach(row => rowObjs.push(new TableRow({
    cantSplit: true, children: row.map((v, i) => mkCell(v, i, false))
  })));
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: tableBorders,
    rows: rowObjs
  });
}
function pageFooter() {
  return new Footer({ children: [new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 0 },
    children: [new TextRun({ children: [PageNumber.CURRENT], font: fontBody, size: 18, color: C.secondary })]
  })] });
}

const children = [];
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 4200, after: 220, line: 920 },
  children: [new TextRun({ text: 'AdaptSort', font: fontHead, size: 40, bold: true, color: C.primary })]
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 180, line: 720 },
  children: [new TextRun({ text: '核心调度算法设计逻辑与六类应用场景分析', font: fontHead, size: 36, bold: true, color: C.primary })]
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 1200, line: 360 },
  children: [new TextRun({ text: '算法设计与真实数据应用说明', font: fontBody, size: 24, color: C.secondary })]
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 260, line: 300 },
  border: { top: { style: BorderStyle.SINGLE, size: 8, color: C.accent, space: 10 } },
  children: [new TextRun({ text: '项目依据：Algorithm-project-main 当前源代码、README 与场景设计资料', font: fontBody, size: 20, color: C.secondary })]
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 0, line: 300 },
  children: [new TextRun({ text: '【作者】    【日期】', font: fontBody, size: 20, color: C.secondary })]
}));

const bodyChildren = [];
children.push(heading('摘要'));
children.push(body('本文围绕项目核心调度算法 AdaptSort，说明其设计目标、输入特征提取、规则短路、本机成本模型与策略选择机制，并概述其在六类真实数据场景中的排序作业及影响。AdaptSort 并非固定采用单一排序方法，而是先按元素类型限定候选算法，再根据输入规模与样本特征识别有序性、逆序程度、值域和重复率等信息；对适合的规模直接执行简单策略，对较大输入则以本机标定成本估计比较排序、计数排序和基数排序的代价，并在可信收益达到切换阈值时选择相应策略。项目的六类领域数据包括电影评分、电商评论、城市骑行、Web 图、航班数据和基因组序列。各领域将自身字段编码为统一的整数键序列，使排序内核得以复用；领域差异主要体现在键域宽窄、近序结构、键值重复程度以及排序在上层任务中的位置。本文同时区分 README 所述六个真实领域与测试集中的六种合成分布，避免混用两种“六类”口径。'));

children.push(heading('一、设计背景与目标'));
children.push(body('传统排序算法通常针对某一类输入和运行环境进行设计：比较排序具有较强通用性，但无法充分利用窄整数值域；计数排序和基数排序在整数数据上可能具有较好的吞吐量，却需要额外扫描、辅助空间或满足键域条件；归并类算法更适合部分近序输入，但其额外空间和数据复制也构成成本。因此，在输入分布、数据规模和目标机器均不确定时，预先固定单一算法往往难以在全部任务上保持合适的代价。'));
children.push(body('本项目将 AdaptSort 设计为一个自适应排序调度器：它既承担排序，也在执行前分析输入并选择排序策略。其目标是在保持排序结果正确的前提下，根据数据特征与机器成本差异，尽可能避免明显不合适的算法路径。README 将总体流程概括为“特征采样—决策函数—策略执行”，其中决策函数包含短路规则和本机标定成本模型。'));

children.push(heading('二、核心算法设计逻辑'));
children.push(heading('（一）类型分流与候选策略集合', HeadingLevel.HEADING_2));
children.push(body('AdaptSort 首先按元素类型划定可用算法。对整数键，调度器可以把比较排序与计数排序、基数排序放在候选集合中竞争；对不能直接按整数值域编码的通用类型，则不执行值域类策略，而在归并排序、内省快排及三路快排等比较类策略之间选择。项目的真实数据应用主要把多字段复合键编码为 int64，因此能够复用整数键路径；按 T 独立保存的成本模型也使不同元素类型不必共用同一组机器成本参数。'));
children.push(heading('（二）输入特征探测', HeadingLevel.HEADING_2));
children.push(body('当输入规模超过极小阈值时，算法从序列中抽取等距样本，探测采样序列的单调性、相邻逆序比例和已排序前缀比例。对整数输入，还会估计样本值域；当比较类策略需要额外信息时，进一步估计重复率。样本探测的目的不是代替最终排序或正确性验证，而是以有限代价形成调度依据。尤其是“逆序率”与“升序前缀比例”联合使用，能够补充区分随机扰动和长前缀近序、块状变化等输入形态。'));
children.push(body('对很小的输入，采样和复杂策略选择的管理成本可能超过排序本身，因此实现采用二分插入排序等简单策略直接处理。对采样显示单调的序列，代码仍进行全量确认：确认已升序后直接返回，确认严格逆序后反转。这使样本仅用于触发快速检查，不会把抽样结果误当作整个序列已经有序的证明。'));
children.push(heading('（三）规则短路与成本模型决策', HeadingLevel.HEADING_2));
children.push(body('完成基本特征探测后，调度器按由低成本到模型决策的顺序处理若干明确情形。极小数组走二分插入；已经全量确认的升序或逆序输入分别走直接返回或反转；较小且逆序比例很低的序列采用插入排序；部分小规模整数输入会在值域估计显示计数排序可能适用时进行精确值域扫描，再依据精确值域决定是否执行计数排序。此类规则避免在有明显捷径时支付完整成本模型比较开销。'));
children.push(body('对较大输入，AdaptSort 将探测特征与本机实测成本结合起来，估算候选策略的耗时。整数键路径会把全量值域扫描本身的代价纳入比较；只有当计数或基数排序预期收益超过扫描成本及相应安全裕度时，才进一步扫描完整输入并确认值域。基数排序还会比较不同数字位宽下的多趟成本。对于比较类候选，算法依据输入是否近序选择归并或内省快排作为基础方案，并将值域类策略与之比较。若最终落在比较排序且数据重复率较高，则可切换到三路快排以减少重复键带来的不必要分区工作。'));
children.push(body('成本模型在首次处理某一元素类型时进行本机标定，覆盖多个输入规模并对策略运行耗时取样，由此为后续估算提供该机器上的成本参数。项目启动或基准执行中可预热成本模型，避免将初始化标定开销混入正式排序耗时。由此，“自适应”的含义是按输入特征和经本机标定的模型作策略调度，而不是在每次排序结束后持续在线学习；策略标签和统计信息主要用于留痕和分析。'));
children.push(heading('（四）调度结果与正确性边界', HeadingLevel.HEADING_2));
children.push(body('决策结果最终落到一种具体排序实现，并记录所采用的策略，以便实验矩阵和应用基准查看策略选择与耗时。实现包括比较排序与整数值域排序路线，且算法内核各有适用条件和空间代价。内省快排设置了深度耗尽后的堆排序回退，可作为比较排序中的最坏复杂度保护；但调度器也可能依据重复率选择没有相同深度保护的三路快排。因此，不宜将单个内核的复杂度保证扩大表述为整个调度器对所有可能输入均有严格 O(n log n) 最坏界。项目通过与标准排序结果逐元素比对等方式检查正确性；性能收益仍需结合实际输入矩阵观察，不应仅凭调度设计推断。'));

children.push(heading('三、六类真实数据场景中的作业与影响'));
children.push(body('README 将应用划分为 A—F 六个真实领域。它们不是六种 C++ 基本类型，而是六类由不同领域数据形成的排序任务。项目通过复合键打包等方式将字段映射为整数键序列，遵循统一数据契约，因此场景扩展主要改变预处理、键位布局与数据元信息，而不必重写 AdaptSort 内核。下表概括各场景排序在上层作业中的作用，以及其数据特征可能怎样影响调度；“预期影响”指机制层面的解释，不等同于每个数据文件都必然触发某一固定策略。'));
children.push(caption('表 1  六类真实应用场景与排序调度关联'));
children.push(makeTable(
  ['场景与数据', '排序作业及上层用途', '对 AdaptSort 调度的影响'],
  [
    ['A 电影评分\nMovieLens 32M', '把电影、用户与评分记录编码为多级整数键，支撑评分数据检索、榜单排序和分组聚合。', '评分值域较窄时，计数排序或其他整数排序策略可能有优势；复合键扩大整体键域后，是否采用值域类策略仍由成本模型和全量值域评估决定。'],
    ['B 电商评论\nAmazon Electronics', '对商品、用户、评分和记录等键进行排序，支撑商品关注度/好评榜单、类目数据处理和价格分组。', 'ASIN 等标识需先映射为整数键；评分与商品标识的组合会改变键域和重复情况，因而可能改变计数/基数与比较排序之间的成本判断。'],
    ['C 城市骑行\nBike', '按站点、时间等字段构造排序键，支持站点统计、出行分析及按月追加的台账数据处理。', '站点计数和按时间编码可产生较窄或结构化的键；台账按块追加时会形成块状近序，低逆序率和较高升序前缀比例可使归并类候选更有竞争力。'],
    ['D Web 图与 PageRank', '按源节点、目标节点键排序以支持 CSR 图结构构建；PageRank 浮点分值经保序映射后复用整数排序内核形成重要性榜单。', '边键的节点编号域与分值映射后的键域不同，会影响值域估计；对浮点分值的保序编码保证可用整数比较语义，不会因直接整数算法而改变原有顺序含义。'],
    ['E 航班数据\nUS DOT On-Time', '按机场、时间和延误等级等字段排序，支持航班延误榜、分桶统计和航司台账分析。', '延误分桶形成的窄整数域可能利于计数排序；按航司分块积累的时间台账则可能呈现局部近序。实际策略仍取决于具体序列规模和样本特征。'],
    ['F 基因组序列\nE. coli K-12', '后缀数组倍增构造中，每轮对 (rank[i], rank[i+k]) 复合键排序，服务于后缀数组、BWT 与后续子串搜索。', '复合秩键使后缀比较转化为整数排序；迭代中键域可能变化，因而每轮可重新触发调度。键域收缩可能提高值域排序的吸引力，但不能据此断言所有轮次都已采用计数排序。']
  ], [20, 38, 42]
));
children.push(body('六类场景的共同影响在于：领域数据的组织方式决定了排序键的编码、键域和局部有序结构，而 AdaptSort 通过统一探测这些特征来适应不同领域任务。场景本身并不直接指定调度策略。比如“窄值域有利于计数排序”是一条条件性原理；调度器还要计入扫描成本、数组规模、机器标定参数和预设收益裕度，因此对任何实际文件的策略结论应以对应运行记录为准。项目的设计范围是六个领域，但当前应用界面可能将城市骑行拆为北京和深圳等数据实例；这不会改变 A—F 六领域口径。'));

children.push(heading('四、与合成测试分布的口径区分'));
children.push(body('项目实验还使用六种合成输入分布评估调度行为，包括正序、逆序、均匀随机、高斯分布、泊松分布及 CVE 替身。它们是排序输入形态，用于检验单调捷径、随机值域、窄域重复和块状近序等特征对选择结果的影响；它们与上一节的六类真实应用领域不是同一组内容。CVE 替身是项目为测试而构造的输入形态，不应表述为真实漏洞样本。实验记录的策略标签可用于观察分布与决策之间的联系，但本文不据此补写未核实的性能数值。'));

children.push(heading('五、总结'));
children.push(body('总体而言，AdaptSort 的核心设计是将“输入分析”和“排序执行”组合为一个统一入口：先根据类型确定策略空间，再用低成本采样获取输入特征，通过短路规则处理明显情形，最后以本机标定成本模型比较适用候选并记录所选策略。该逻辑使同一排序内核能够服务六类领域排序作业，并将各领域差异吸收到键编码与运行时特征中。其效果取决于特征估计质量、成本模型与输入规模，因此应通过当前实现的矩阵实验、正确性比对及具体策略日志验证；对预期调度收益和复杂度保证均应作边界明确、与证据一致的表述。'));

children.push(heading('主要项目依据'));
children.push(body('本文内容依据项目 README.md 第六节“算法要点”、第七节“实验设计与结果口径”、第九节“多场景扩展”，以及 cpp/adaptsort/adaptsort.h、cpp/adaptsort/algorithms.h、cpp/main_cpp.cpp、app/sufarray.h 和 app/多场景应用方案.md 整理。实现细节以目标项目当前源代码为准；场景适配部分结合 README 中 A—F 场景表与场景设计说明概括。'));

const coverChildren = children.slice(0, 5);
const bodyChildrenFinal = children.slice(5);
const coverTable = new Table({
  width: { size: 100, type: WidthType.PERCENTAGE },
  layout: TableLayoutType.FIXED,
  borders: coverNoBorders,
  rows: [new TableRow({
    height: { value: 16838, rule: 'exact' },
    cantSplit: true,
    children: [new TableCell({
      shading: { type: ShadingType.CLEAR, fill: C.white },
      borders: coverNoBorders,
      verticalAlign: 'top',
      children: coverChildren
    })]
  })]
});

const doc = new Document({
  creator: 'OpenAI',
  title: 'AdaptSort核心调度算法设计逻辑与六类应用场景分析',
  subject: '算法设计与真实数据应用说明',
  description: '根据 Algorithm-project-main 当前实现与项目说明整理。',
  styles: {
    default: { document: {
      run: { font: fontBody, size: 24, color: C.body },
      paragraph: { spacing: { line: 312 } }
    } },
    heading1: { run: { font: fontHead, size: 32, bold: true, color: C.primary }, paragraph: { spacing: { before: 300, after: 120, line: 360 }, outlineLevel: 0 } },
    heading2: { run: { font: fontHead, size: 28, bold: true, color: C.primary }, paragraph: { spacing: { before: 220, after: 100, line: 340 }, outlineLevel: 1 } }
  },
  sections: [
    {
      properties: { page: { size: { width: 11906, height: 16838, orientation: PageOrientation.PORTRAIT }, margin: { top: 0, bottom: 0, left: 0, right: 0 } } },
      children: [coverTable]
    },
    {
      properties: { type: SectionType.NEXT_PAGE, page: { size: { width: 11906, height: 16838, orientation: PageOrientation.PORTRAIT }, margin: twip, pageNumbers: { start: 1, formatType: NumberFormat.DECIMAL } } },
      footers: { default: pageFooter() },
      children: bodyChildrenFinal
    }
  ]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(OUT, buffer);
  console.log(OUT);
}).catch(error => { console.error(error); process.exit(1); });
