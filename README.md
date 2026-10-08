# 题目二：排序算法性能比较与应用（统一版）

统一了实验内容 1~6（八种排序算法）与实验内容 7（AdaptSort 普适排序）的代码，
并新增 **C vs C++ 语言对比实验** 与 **统计模块完善版**。

## 快速开始（新队友三步跑起来）

```bat
:: 0) 环境要求：Windows + MinGW-W64 GCC（gcc/g++ 同版本）+ Python 3.10+；可选本地代理
:: 1) 下载原始数据（约 1.5GB，断点续传，中断后重跑即可；可带代理参数）
prepare_data.bat http://127.0.0.1:7897
::    ——等价于 python app\pre\download_data.py + 六个场景预处理 + 双构建 + PageRank/后缀数组
:: 2) 跑核心基准（评分点 1~7，各几分钟）
build\sort_demo_c.exe  &  build\sort_demo_cpp.exe
python tools\compare_c_cpp.py
:: 3) 起应用（评分点 8/9/10：Web UI + 多场景）
build\sort_demo_app.exe server      :: 浏览器打开 http://127.0.0.1:8080
```

克隆仓库即带小规模抽样档（1e5/1e6 键）、各场景 `entities.dat`/meta/UI-JSON 与
全部结果 CSV，gene 场景的 `seq.txt`+`sa.dat` 也已入库——**第 2、3 步不需要下载
全量数据即可运行**（含 gene 子串搜索）；要复现全量矩阵（10⁶~3.2×10⁷ 键）与
六场景完整数据，执行第 1 步即可。原始数据来源与下载日期见"多场景扩展"一节
与各场景 `app/data/<scn>/stats.txt`（含来源 URL，可追溯）。

> 提示：大文件（bikecn/bikesz 的全量 postings/ledger、movie 全量 postings 等）
> 不入库（单项 25~81MB，可由脚本精确重建），由 `download_data.py` +
> `pre_<scn>.py` 一键再生。例外：gene 的 `sa.dat`（18.6MB）已随仓库提供，
> 子串搜索开箱即用；如需重建：`build\sort_demo_app.exe sufarr app\data\gene`。

## 一、工程结构

```
排序算法/
├── common/               # 双语核心：同一份源码，gcc 当 C 编译、g++ 当 C++ 编译
│   ├── sortstats.h       #   统计模块完善版（实验内容 5）：计数/计时分离、峰值附加空间、QPC 高精度计时
│   ├── seqlist.h         #   实验内容 1：顺序表 SeqList
│   ├── datagen.h         #   实验内容 2/6：5 类分布生成器（语义与 v1 一致）+ CVE替身（扩展）
│   ├── sortcore.h        #   实验内容 3/4：8 种排序算法（含统计挂钩、附加空间登记）
│   └── bench.h           #   基准测量框架：等时长批量计时、min/中位数/标准差、计数模式单列
├── c/
│   └── main_c.c          # C   驱动：正确性 + 计数自检 + 全矩阵（8 算法 + qsort 基线）
├── cpp/
│   ├── adaptsort/        # 实验内容 7：AdaptSort（req7 v3 原样引入：adaptsort/algorithms/counters）
│   └── main_cpp.cpp      # C++ 驱动：同矩阵（8 算法 + AdaptSort + std::sort 基线）
├── tools/
│   └── compare_c_cpp.py  # 汇总两份 CSV → C vs C++ 对比表（含计数一致性断言）
├── app/                  # 评分点 8/9/10：CineRank 应用（MovieLens 真实数据）
│   ├── preprocess.py     #   一次性预处理：ratings/movies CSV → .dat 数据文件
│   ├── sortgen.h         #   8 算法的 C++ 模板泛型版（int64 posting 键；与 sortcore.h 同口径）
│   ├── ml_index.h        #   movie 场景数据层：倒排索引聚合 / 检索 TopN / 多级键榜单 / 分组
│   ├── entity_index.h    #   多场景通用实体层：键位布局参数化（meta.txt）+ 通用榜单/检索/直方图
│   ├── webgraph.h        #   场景 D：CSR + PageRank + IEEE-754 浮点→int64 保序映射
│   ├── sufarray.h        #   场景 F：倍增后缀数组（对拍自检）+ BWT + O(m log n) 子串搜索
│   ├── pre/              #   各场景预处理脚本 common.py + pre_{web,amazon,bike,flight,gene}.py
│   ├── make_package.py   #   生成转发清理包（排除 build/ 与可精确再生的大 .dat）
│   ├── server.cpp        #   单二进制七模式：selftest / matrix / extsort / topk / pagerank / sufarr / server
│   ├── 多场景应用方案.md  #   B~F 五场景扩展方案（Amazon/共享单车/Web图/航班/基因组）
│   ├── frontend/         #   浏览器前端（排序动画 + ECharts 基准图 + 多场景应用 + 六场景总览）
│   └── data/             #   <scn>/ 按数据契约存放（见第九节）
├── build_app.bat         # 应用一键编译
├── build.bat             # 一键编译两个语言版本（第 1~7 点）
└── results/              # 运行输出：*_c.* / *_cpp.* / c_vs_cpp.md / results_real.csv 等
```

## 二、环境与编译

- 编译器：MinGW-W64 GCC 13.2.0（gcc 与 g++ 同版本同后端 —— 语言对比实验的前提）
- 编译：`build.bat`，等价于
  ```
  gcc -O2 -std=c11   -Wall -D__USE_MINGW_ANSI_STDIO=1 -I common c\main_c.c      -o build\sort_demo_c.exe
  g++ -O2 -std=c++17 -Wall -I common -I cpp\adaptsort cpp\main_cpp.cpp          -o build\sort_demo_cpp.exe
  ```
- 运行（在工程根目录）：`build\sort_demo_c.exe` → `results/results_c.{txt,csv}`
  `build\sort_demo_cpp.exe` → `results/results_cpp.{txt,csv}`
- 对比汇总：`python tools\compare_c_cpp.py` → `results/c_vs_cpp.{md,csv}`

## 三、统一设计要点

1. **同源码双构建**：8 种算法只写一份（`common/sortcore.h`），分别以 C 和 C++ 编译。
   生成器同源同种子 → 两个构建在每个 (分布, 规模) 单元的输入**逐字节相同**，
   因此比较次数/移动次数在两个构建下**必须完全一致**（`compare_c_cpp.py` 断言），
   时间差异即纯粹的语言实现差异。
2. **AdaptSort 无缝接入**：C++ 驱动把 req7 的 `adapt_sort` 包装成统一接口
   `void f(int*, int, int order, SortStats*)`，内部经 `sb::Counters/sb::mem`
   桥接出比较/移动/峰值附加空间与决策策略（CSV 的 strategy 列）。
3. **两种库基线**：C 侧 `qsort`（函数指针比较器），C++ 侧 `std::sort`（内联比较器）
   —— 构成语言设施层面的对照。

## 四、统计模块完善版（实验内容 5）

相比 v1 的四点改进：

| 项 | v1 旧版（已归档） | 完善版 |
|---|---|---|
| 计数/计时分离 | 计数自增始终发生，计时被计数开销污染 | st=NULL 时为纯计时模式；计数模式另跑一遍，两列分立 |
| 计时精度 | `clock()`，MinGW 下 1ms 分辨率（v1 的 n=1000 档全是 0.00ms） | `QueryPerformanceCounter`，~100ns 分辨率，n=100 也有 3 位有效数字 |
| 小规模测量 | 逐次计时，n 小时被量化与抖动淹没 | 等时长批量：批量 B 使整批 ~8ms，计时后除以 B；拷贝工作集上限 8MB |
| 附加空间 | 无 | salloc/sfree 登记出口记录峰值附加字节，验证 O(1)/O(n)/O(n+k) 论断 |
| 统计口径 | 5 次取最小 | 最小值（主口径，负载是单侧噪声）+ 中位数 + 标准差三列齐全 |
| 计数自检 | 无 | TC-4 理论值对照：插入正序 n-1 次/2(n-1) 次，冒泡正序 n-1 次/0 次，选择恒 n(n-1)/2，基数恒 0，快排全等输入三路一趟 |

计数口径（与 req7/counters.h、严蔚敏教材一致）：一次关键字比较记 1；
一次赋值记 1，一次三赋值交换记 3；基数排序的散射与整块拷回均按逐元素计入。
递归栈空间不属于堆附加空间，不计入 aux_peak。

## 五、C vs C++ 对比实验（结果见 results/c_vs_cpp.md）

实验设计回答两个问题：

1. **同一份 C 风格算法源码，gcc(C) 与 g++(C++) 编译后性能是否有差异？**
   （两个编译器同版本、同后端；预期差异在噪声内 —— 结论以运行结果为准）
2. **语言设施差异是否可测？** `qsort` 的比较器是函数指针，每次比较都要间接调用；
   `std::sort` 的比较器模板被内联进排序循环。两者之差是 C/C++ 抽象成本差异的
   教科书案例。AdaptSort 依赖模板与 `if constexpr`，是 C++ 泛型设施支撑
   "同一套代码适配多类型策略"的实例。

## 六、算法要点（8 种自实现 + AdaptSort）

- 直接插入/冒泡（带提前终止）/选择：O(n²) 基线；选择排序移动次数最少
- 希尔：Shell 增量 n/2 递减
- 快速：xorshift32 随机基准 + 三路划分 + 小区间(≤16)插入 + 尾递归消除
- 堆：方向化堆（升序大顶堆/降序小顶堆）
- 基数：LSD 按字节 4 趟，最高字节翻转符号位；降序 = 原地反转；比较数恒 0
- 归并：递归 + O(n) 辅助数组，稳定
- AdaptSort：特征采样 → 决策函数（短路规则 + 本机标定成本模型）→ 8 策略执行，
  含 O(n log n) 最坏界兜底。统一版副本为 **v3.1**：在 req7 v3 基础上把基数
  数字位宽（8/16）纳入成本模型按本机标定竞争、标定规模补齐 n=10² 档、
  值域类切换裕度按模型可信度拆分（计数 1.30 / 基数 1.10）——
  实现了 req7 主文档 §11.6 记录的既定下一步，改动明细见
  `cpp/adaptsort/CHANGES-v3.1.md`；req7 原件保持 v3 不动

## 七、测试矩阵

- 分布（6）：正序、逆序、均匀、高斯、泊松 + CVE替身（扩展，统计等价于真实
  CVE 语料的"高重复 + 近似有序"形态）
- 规模（5）：10²、10³、10⁴、10⁵、10⁶
- 算法：C 构建 9 个（8 自实现 + qsort），C++ 构建 10 个（8 自实现 + AdaptSort + std::sort）
- Θ(n²) 三算法在 n>10⁴ 档不实跑（CSV 中记 -1），避免数小时量级的等待
- 全部种子固定，结果可复现；正确性 768 组/构建 + 计数自检 6 项/构建

## 八、CineRank 应用（评分点 8/9/10）

**应用**：基于 GroupLens MovieLens 真实评分数据的电影检索与排序系统。
题目问题描述点名的"影视作品按关注度、好评度排序，搜索结果按重要性显示"
即本应用。排序是系统四处关键子问题：倒排索引构建（posting 键排序）、
电影榜单（多级键编码排序）、检索 Top-N（堆选择）、类型分组（排序+扫描）。

**数据管线**（一次性）：

```
python app/pre/_fetch_ml32m.py                    # 下载 ml-32m（3200 万评分，收录至 2023）
python app/preprocess.py downloads/ml-32m app/data
```

rating 三元组 (movieId, userId, ratingIdx) 打包为单个 int64 键
`key = movieId<<22 | userId<<4 | ratingIdx`（≤41 位），排序即建索引；
导出 postings.dat（全量 3.2×10⁷ 键）与 1e5/1e6 抽样档（规模阶梯）。

> 数据集版本说明：早期版本用 ml-25m（收录截止 2019，2020 年后无评分），
> 现改用 **ml-32m**（8.76 万部影片、收录至 2023，2020 年后影片约 7800 部）。
> 两者 movieId 体系一致，键编码与排序内核零改动——只需换数据目录重跑 preprocess。
> 换数据集后建议同步刷新派生数据与基准：
> `python -c "import app.pre._enrich_more as m; m.movie()"`（单片评分分布）与
> `sort_demo_app matrix`（真实数据矩阵 → results_real.csv）。

**五个运行模式**：

| 命令 | 内容 | 对应评分点 |
|---|---|---|
| `sort_demo_app selftest` | int64 合成+真实数据正确性（与 std::sort 逐元素比对）+ 计数自检 | 8 |
| `sort_demo_app matrix` | 真实数据排序矩阵（三档规模 × 8 自实现 + AdaptSort + std::sort）→ results_real.csv | 8 |
| `sort_demo_app extsort` | 外部排序 BSBI：内存 1/10 限流，块内 AdaptSort + k 路堆归并，统计 I/O 块 | 10 |
| `sort_demo_app topk` | Top-K 对比：堆 O(n log k) vs 快速选择 O(n) vs 全排序 | 10 |
| `sort_demo_app server` | HTTP 服务（127.0.0.1:8080）+ 浏览器 UI | 9 |

**Web UI（app/frontend/index.html，echarts 本地化）**：

- 算法实验室：排序过程逐帧动画（9 算法 × 5 分布 × n≤10⁴ 自动抽帧，AdaptSort 先真实
  决策再回放所选策略），比较/移动计数器随帧同步；现场单格实测按钮
- 性能基准：时间-规模折线（C/C++ 双构建 + 真实数据三数据源切换）、真实数据
  耗时/操作次数条形图、C vs C++ 语言对比图、矩阵明细表
- 电影应用：多级键榜单（关注度/好评度优先切换、类型/票数筛选）、影片检索
  Top-N（堆选择）、数据集画像（年份/类型分布）

**键编码与多级键**：榜单模式 pop 用 `count<<8|(63-avg×10)`（关注度主键）、
模式 rat 用 `avgX10<<24|count`（好评度主键），行号并入低位保证唯一 ——
"多级复合键 → 单 int64 键"的编码路径直接复用第 3/4 点算法；与之对照的
"逐级稳定排序"路径见报告。

**int64 算法来源**：`app/sortgen.h` 为 8 算法的模板泛型版，算法逻辑/优化点/
计数口径与 common/sortcore.h 逐语句一致（模块状态按 K 类型隔离，可与 int 版
共存于同一编译单元）；AdaptSort 经 `sb::cost_model_for<T>()` 按 T=int64
独立标定成本模型（int64 访存量翻倍，int 标定系数会给决策引入系统偏差）。

## 九、多场景扩展：一套排序框架 × 六个真实领域

题目原文列举"数据压缩、计算生物学、供应链管理、组合优化、调度"等领域中
"排序是关键子问题"。在 movie 场景之外新增五个真实数据场景，共用同一套
排序内核（common/ + sortgen.h + AdaptSort）、同一份多级键编码方法与同一份
数据契约，**排序内核与 UI 框架零改动**——新增场景只需 ①一个预处理脚本
②meta.txt 里的键位参数。

| 场景 | 数据源（均为直链下载） | 规模 | 键打包 | 该场景独有的算法结论 |
|---|---|---|---|---|
| A movie | GroupLens MovieLens 32M | 3.2×10⁷ 评分 | mid<<22\|uid<<4\|ridx | 窄评分域计数 25×；多级键榜单；收录至 2023 年 |
| B amazon | SNAP ratings_Electronics.csv（318MB） | 7.82×10⁶ 评论 / 4.76×10⁵ 商品 | item<<28\|user<<4\|ridx（用户实际 4.8M，22 位不够已扩 24 位） | ASIN 稠密化=键域变换；关注/好评双榜单 |
| C bike | s3 tripdata 2019 全年 zip（858MB） | 1.28×10⁷ 骑行 / 954 站 | station<<21\|minute | 站点计数排序；台账序=真实块状近序（ledger.dat） |
| D web | SNAP web-Google.txt.gz | 5.1×10⁶ 边 / 8.76×10⁵ 节点 | src<<20\|dst | CSR 建图排序瓶颈；PageRank 经 IEEE-754 保序映射复用整数内核（score.dat） |
| E flight | US DOT On-Time 2019（直链/代理） | 已取得 4 个月（1,10,11,12）2.42×10⁶ 航班 | origin<<25\|minute<<4\|delayIdx | 长尾延误分桶=计数排序；航司台账近序 |
| F gene | NCBI RefSeq E. coli K-12（1.38MB gz） | 4.64×10⁶ bp | 倍增轮复合键 (rank[i],rank[i+k]) | 排序占后缀数组构造 ~90%；键域随轮次收缩、AdaptSort 策略随之变化；O(m log n) 子串搜索 |

**数据契约**（`app/data/<scn>/`，由 `app/pre/pre_<scn>.py` 流式生成）：

```
postings.dat        uint64 键序列 —— 场景的大排序负载（全量）
postings_1e6/1e5    等距抽样档（矩阵规模阶梯）
entities.dat        实体台账 TSV：id/year/category/val/name
ledger.dat          (C/E) 台账序键 —— 块内有序、块间拼接的"块状近序"
score.dat           (D) PageRank 分值经保序映射后的排名键
sa.dat/rounds.csv   (F) 后缀数组与每轮排序耗时/策略记录
records.jsonl.gz    全量清洗记录 —— **JSON Lines + gzip**（"爬取成 JSON 格式"
                    的交付形态；文本压缩 ~8x，控制磁盘占用 ≤5GB 预算）
records_sample.json 前 200 条记录直观 JSON 数组（免解压查看）
meta.txt / meta.json 键位布局与标签（C++ 读 txt，UI 读 json）
stats.txt           数据体检摘要（含来源 URL 与下载日期，可追溯）
```

**新增运行模式**：

| 命令 | 内容 |
|---|---|
| `sort_demo_app matrix app/data/<scn>` | 该场景真实数据排序矩阵 → `results/results_real_<scn>.csv` |
| `sort_demo_app extsort app/data/<scn>` | 该场景外部排序（BSBI）→ `results_extsort_<scn>.txt` |
| `sort_demo_app topk app/data/<scn>` | 该场景 Top-K 三方法对比 → `results_topk_<scn>.csv` |
| `sort_demo_app pagerank app/data/web` | PageRank 幂迭代 + IEEE-754 保序映射 + 映射自检 |
| `sort_demo_app sufarr app/data/gene` | 倍增后缀数组（对拍自检）+ BWT + 每轮 AdaptSort 策略记录 |

**多场景 API**（评分点 9 UI 的"应用/六场景总览"页）：`/api/scn/list`、
`/api/scn/stats?scn=`、`/api/scn/search`、`/api/scn/topk`、`/api/scn/meta`、
`/api/gene/info`、`/api/gene/search?pat=ACGT…`（后缀数组二分搜索演示）。

**打包口径**：全量大键集（bikecn/bikesz 的全量 postings/ledger，单项 25~81MB）
体积大且可由预处理脚本 + 原始数据精确再生，默认不入库/不随包转发（`make_package.py`
已排除 build/ 与上述大文件）；gene 的 `sa.dat`（18.6MB）随仓库提供（子串搜索
开箱即用）；`records.jsonl.gz`、抽样档、meta、stats 与全部 results 随包。

## 十、数据再生成与转发说明

`app/data/` 下的 .dat 由 `preprocess.py` 从 MovieLens 原始 CSV 一次性生成：

```
# 1) 下载数据集（约 262MB，直连慢，建议走系统代理分段并行）
curl -x http://127.0.0.1:7890 -L -o downloads/ml-25m.zip \
     https://files.grouplens.org/datasets/movielens/ml-25m.zip
# 2) 解压并预处理（25M 行约 25 秒）
unzip downloads/ml-25m.zip -d downloads
python app/preprocess.py downloads/ml-25m app/data
```

- **全量 `app/data/postings.dat`（200MB）默认不随包转发**：体积大且可由上述
  两步精确再生成（键序列确定性依赖输入顺序，固定数据集下逐字节可复现）。
  小样本 `postings_1e5/1e6.dat` 与 `movies.dat` 已随包，程序会自动回退到
  最大可用抽样档 —— 接收方解包后无需下载即可编译并运行 `selftest`、
  `server`（UI 完整可用）与 `matrix` 的 10⁵/10⁶ 档；跑 2.5×10⁷ 全量档
  按上述步骤再生数据即可。
- 编译产物（.exe）不随包：接收方用 `build.bat` 与 `build_app.bat` 从源码重建
  （MinGW-W64 GCC 13.2.0，需 -lws2_32）。
- `results/` 内的全部实验输出（含 25M 全量矩阵、外部排序、Top-K、C vs C++）
  为既有运行证据，随包转发，无需重跑即可引用。
