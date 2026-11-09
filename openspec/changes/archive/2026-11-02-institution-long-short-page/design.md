# Design

## Context

见 `proposal.md - Why`。本设计基于对现有代码与数据源的实际核查：

- 前端为 React 19 + Vite + Tailwind 4，路由在 `src/App.tsx`，页面目前多为 `ChartPlaceholder` 占位，尚无图表库；设计系统变量在 `src/index.css`（`brand` 主色 `#3a9a6a`、`ocean` 信息蓝、`coral` 下跌、`mint` 上涨）。
- 数据层已有 `src/data/{types,client,MarketDataProvider}.tsx`，`/api/snapshot` 30 秒轮询；服务端 `server/{app,snapshot,scheduler,paths}.py`；流水线 `pipeline/` 扁平 import，产物经 `io_utils.protected_write` 原子写入并由 `schemas.py` 校验。
- 指数日线已落盘于 `data/raw/indices-ths/sh000300.csv`（2020-10-30 起，1455 行），可直接作为"次日沪深300"基准，无需新增行情源。
- 数据源实测（2026-10-31 于本项目 `.venv`，akshare 1.20.18）：
  - `ak.get_cffex_rank_table(date="20240105", vars_list=["IF","IC","IH","IM"])` 返回 `dict[合约代码 → DataFrame]`，四品种一次调用耗时约 **0.4 秒**，当日 13 个合约共 273 行。
  - 每个合约固定 **20 行排名 + 1 行 `rank=999` 合计行**（`party_name` 为 `None`，即该合约前 20 会员合计），后者可作为聚合守恒的校验基准。
  - 会员名称**无"期货"后缀**：实测为 `国泰君安`、`中信期货`，且同一天同时存在 `中信建投`——按子串匹配会把两者混为一谈。
  - 非交易日返回**空 dict** 并发出 `UserWarning: YYYYMMDD非交易日`，不抛异常。

## Goals / Non-Goals

**Goals:**

- 把"合约 × 会员"的日度明细稳定加工为"机构 × 品种 × 交易日"的净持仓序列，口径可复现、可对照校验。
- 让"当日多空动作 vs 次日大盘走势"的同向/对冲关系成为可筛选、可画线、可带样本量统计的一等数据。
- 首次全量抓取控制在 10 分钟内，日常增量在秒级完成。

**Non-Goals:**

- 不构建交易信号，不把同向率接入 `confirmed_index_signals` / `confirmed_sector_trends`。
- 不做全市场持仓估算、不做会员持仓的真实方向推断（无法区分套保与投机）。
- 不做名义金额加权、跨品种归一化（列为后续可选项，见 Open Questions）。
- 不回溯 3 年以前，不接入期权持仓。

## Decisions

### D1. 数据获取：逐交易日抓取中金所排名，按日落盘缓存

按交易日循环调用 `get_cffex_rank_table(date, vars_list=[IF,IC,IH,IM])`，每个交易日把四品种的原始明细合并后写为 `data/raw/cffex/{YYYY-MM-DD}.csv`（与 `data/raw/` 同策略不入库）。交易日历**直接取 `data/raw/indices-ths/sh000300.csv` 的 `date` 列**，而不是自建节假日表——保证机构序列与指数序列日期轴天然对齐，也避免"中金所交易日 ≠ 沪深交易所交易日"的边界问题。

- 非交易日（返回空 dict）**不写缓存文件**，视为无数据，下次运行重试。
- 已存在且非空的缓存文件直接复用，不发起远端请求；增量构建只补缺失日期。
- 全量 730 个交易日 × 0.4 秒 ≈ 5 分钟（含重试退避约 5–10 分钟）。

替代方案：直接解析中金所 `cffex.com.cn` 的 txt/dat 文件（更快，但需自行处理编码与字段变更）；或 `ak.futures_zh_daily_sina` 等行情接口（无持仓排名）。选择 akshare 是因为项目已依赖它、字段已结构化、口径稳定。

### D2. 会员归一：显式映射表 + 精确匹配，禁止子串匹配

维护 `MEMBER_ALIASES: dict[str, str]`，把会员全称映射到规范名，再按规范名归入 `guotai` / `citic` / `others`。映射采用**精确匹配**（先 strip 全角空格），不做 `in` 判断——实测同日存在 `中信期货` 与 `中信建投`，子串匹配会直接污染口径。未在映射表中的会员一律计入 `others`。

替代方案：用正则匹配"国泰""中信"前缀。已否决，理由同上，且未来新增"中信证券"等主体时会静默错分。

### D3. 聚合层级：先会员跨合约汇总，再分组求和

聚合顺序固定为：`(date, variety, member) → long/short`（跨该品种所有合约相加，剔除 `rank=999` 行）→ 按分组求和 → 四品种汇总（各品种直接相加）。

- 使用 `rank <= 20` 过滤排名行；`rank=999` 行单独保留，作为**守恒校验**：任一日任一品种，三家分组的多单合计 MUST 等于该行 `long_open_interest`，空单同理。该断言在构建时执行，偏差非 0 即失败。
- 汇总视图为手数简单加总，产物元信息携带 `aggregation_note` 说明未按合约乘数折算，页面必须展示。

### D4. 净持仓变化：按品种的"上一交易日"，非上一次抓取日

对每个 `(机构, 品种)` 序列，按产物日期序列取前一交易日计算 `Δnet`。若前一交易日在产物中缺失（例如历史起点或抓取空洞），该日 `Δnet` 置空并标记 `chg_missing`，绝不用 0 或跨日差值填充。

### D5. 次日关系：以指数日线序列的下一行定义"次日"

"次日"取 `sh000300` 日线中该日期的**下一行**，而非"日期 +1 自然日"。次日收益率 `next_ret = close_{t+1}/close_t - 1`；关系判定：

| 条件 | 关系 |
| --- | --- |
| `Δnet > 0` 且 `next_ret > 0`，或 `Δnet < 0` 且 `next_ret < 0` | 同向（`S`） |
| `Δnet > 0` 且 `next_ret < 0`，或 `Δnet < 0` 且 `next_ret > 0` | 对冲（`H`） |
| `Δnet == 0` 或次日不存在 | 待判定（`P`） |
| 当日持仓缺失 | 无数据（`N`） |

同向率分母 = `S + H`，`P` 与 `N` 均不计入，但条数单独输出。统计按 `(机构, 品种)` 与全区间汇总各输出一份。

### D6. 产物结构：共享日期轴 + 定长数组 + 单字符关系码

`data/institution-positions.json` 采用紧凑结构控制体积（730 日 × 3 组 × 5 视图 ≈ 11k 条记录）：

```
as_of, generated_at, window{start,end}, varieties, institutions[{id,name,note}],
aggregation_note, dates[], 
series: { guotai: { all: { long[], short[], net[], chg[], next_ret[], relation[] }, IF: {...}, ... }, citic: {...}, others: {...} },
stats: { guotai: { all: {rate, decided, same, hedge, pending, start, end}, ... }, ... },
quality: { missing_dates[], conservation_checked, source }
```

数组与 `dates` 等长、同下标对齐；缺失值用 `null`（JSON 原生，前端断点渲染）。关系用单字符码，前端映射为中文标签。

替代方案：对象数组（可读性好但体积约 3–4 倍，且无法与日期轴天然对齐）。已否决。

### D7. 接口：独立端点 + 进程内 mtime 缓存

新增 `server/institutions.py`：读取产物 → 按文件 mtime 缓存 → 按 `institution/variety/from/to/limit` 切片返回。`/api/snapshot` 仅追加 `institutions` 摘要（最新交易日 + 三家净持仓 + 同向率），不内联长序列，避免 30 秒轮询体积膨胀。

参数校验在服务端完成（枚举 + 日期格式 + 区间方向），非法值返回 400 并列出允许值。

### D8. 前端图表：引入 ECharts，双轴折线

净持仓（手）与沪深300 涨跌幅（%）量纲差异大，需要双 Y 轴 + 十字光标 + 750 点断点渲染 + 图例交互，手写 SVG 的实现与维护成本明显高于引入 ECharts。

- **2026-11-02 更新**：`echarts@^5.6.0` 已存在于 `signal-hub-web/package.json`，直接复用，不再写入依赖。
- 按需引入（`echarts/core` + `LineChart` + `GridComponent`/`TooltipComponent`/`LegendComponent`/`DataZoomComponent`），路由级 `lazy` 加载。
- 配色从 `index.css` 的 CSS 变量取值（`--color-brand-500`、`--color-ocean-500`、`--color-mint-500`、`--color-coral-500`），保持与既有设计系统一致。

替代方案：手写 SVG 折线（零依赖，与当前 `ChartPlaceholder` 风格一致）。已否决：双轴刻度、缩放、tooltip 命中、断点处理均需自建，成本高于收益。

### D9. 页面结构：口径优先、图表居中、明细在下

`/institutions` 自上而下：口径与事后统计声明 → 品种切换（汇总/IF/IC/IH/IM）+ 时间范围（20/60/120 日）→ 三家摘要卡（多单/空单/净持仓/日变化/次日涨跌/关系/同向率+样本量）→ 双轴折线图 → 历史明细表（默认 50 行/页，可按机构、品种、日期区间筛选）。

**2026-11-02 更新**：现有实现路由为 `/institution`（`App.tsx` 与 `Sidebar.tsx`），本变更统一更正为 `/institutions`。页面文件沿用现有 `src/pages/InstitutionPositions.tsx` 原地重写（保留路由懒加载入口），废弃 `src/data/institutionClient.ts`；页面 MUST NOT 包含任何 mock 数据或写死数值（现有实现违反 spec「加载、失败与陈旧降级」要求，整体废弃）。

### D10. 刷新策略：日频数据不参与 30 秒轮询

机构持仓为日频产物，页面**只复用数据层的模式与时点展示**（`as_of`/`mode`/`stale`），机构数据本身在进入页面时请求一次，并提供手动刷新；不随 30 秒快照轮询重复拉取 750 日序列。这与 `live-data-ui` 的"30 秒轮询"要求不冲突——该要求约束的是快照端点，机构端点为独立的日频查询。

### D11. 收盘更新挂载点

在 `pipeline/bootstrap.py` 的 `refresh_daily()` 链路末尾追加机构持仓增量更新，失败不阻断指数与板块刷新（机构产物独立，缺失时前端显示"数据准备中"）。

### D12. (经纪) 后缀会员归一（2026-11-02 用户确认）

核查 `quality.unmatched_members` 发现：2024-02-26 至 2024-04-26 共 **43 个交易日**，中金所源数据的会员名整体带"(经纪)"后缀（如 `中信期货(经纪)`、`国泰君安(经纪)`，同期 `中信建投(经纪)` 亦存在），精确匹配映射未覆盖，导致这些交易日的中信/国泰持仓全部落入 `others`，三家分组序列与相邻日 `Δnet` 失真。

- 处理：`MEMBER_ALIASES` 追加两条**精确**别名（`中信期货(经纪)` → 中信期货、`国泰君安(经纪)` → 国泰君安），不引入子串匹配（D2 原则不变）；`(经纪)` 后缀的其他会员本就在 `others`，无需处理。
- 重建：原始缓存 `data/raw/cffex/` 未受影响（缓存的是原始明细），用本地缓存重建产物即可，**不重新抓取远端**；重建后 `unmatched_members` 不应再含这两条名称，守恒校验不受影响（`others` 为剩余组，天然守恒）。
- 留痕：按 AGENTS.md 第 9 条，重建前后同向率（含 `decided` 样本量）对比写入变更说明；统计区间与调参无关，属数据修复而非口径变更。

### 构建记录（任务 4.4，2026-10-31 构建 / 2026-11-02 核实）

- 产物 `data/institution-positions.json` 约 631KB，`algorithm_version=institution-positions-cffex-top20-v1.0`。
- 窗口：2023-10-30 → 2026-10-30，共 **728 个交易日**；`quality.missing_dates` 0 条、`conservation_checked=true`、`cached_days=728`。
- 同向率统计（修复 (经纪) 前的基线）：各机构 × 各品种视图 `decided=684`、统计区间 2023-10-31 → 2026-10-29；如国泰君安汇总视图 rate≈0.478（same 327 / hedge 357 / pending 44）。重建后基线以任务 9.3 的前后对比为准。

### 口径修复留痕（任务 9.3，2026-11-02 执行）

修复方式：`BOOK_SUFFIX` 正则追加 `经纪` 后缀（与既有 `(代客)` 机制一致，保持精确匹配、未引入子串匹配），用本地缓存重建，未重抓远端（耗时 11.6s，728/728 天，缺失 0，守恒校验 2912 项全过）。修复前 43 个受影响日的中信/国泰净持仓为 0（数据落入 others，关系均为待判定）；修复后 43/43 天归组成功，`unmatched_members` 不再含 `中信期货(经纪)`/`国泰君安(经纪)`。前后对比（汇总视图 `all`，完整快照见 `stats-before-broker-fix.json` / `stats-after-broker-fix.json`）：

| 机构 | 修复前 rate（decided / S / H / P） | 修复后 rate（decided / S / H / P） |
| --- | --- | --- |
| 国泰君安 | 0.4781（684 / 327 / 357 / 44） | 0.4841（725 / 351 / 374 / 3） |
| 中信期货 | 0.4876（683 / 333 / 350 / 45） | 0.4897（725 / 355 / 370 / 3） |
| 其他机构 | 0.5083（726 / 369 / 357 / 2） | 0.5028（726 / 365 / 361 / 2） |

性质：数据修复（把误分组的既有持仓归位），非口径变更；样本量变化仅来自 43 个误判日归位。

### 集成验收记录（2026-11-02）

- **E2E**：最新代码经 8001 端口启动服务、Vite 代理进入 `/institutions`。验证三家固定顺序、默认四品种汇总、IF 品种切换后快照/图表口径/历史明细同步、图表机构筛选、近 20/60/120 日切换、中信期货筛选后 120 行 / 3 页、无结果日期空状态、旧路由 `/institution` 重定向至 `/institutions`；导航无 console 错误（仅 Vite/React 开发提示）。
- **信号回归**：`compare_snapshot.py --baseline-sectors __skip_sectors__.json` 现场重建指数数据后 `PASS：信号事实与基线完全一致`（7 个指数、73 条已确认信号）。板块 strength 基线历史差异与本次机构变更无关，未纳入本次验收结论。
- **既有问题豁免（用户确认）**：全量 `pytest pipeline server -q` 为 217 passed / 1 failed；唯一失败 `pipeline/run_self_test.py::test_openspec_validate` 检查的是已不存在的 OpenSpec 变更 `topbottom-signal-external-survey`，属于历史遗留元数据校验，不影响行情数据、机构口径或信号事实，本次不修。
- **口径抽检**：抽样 2026-10-30、2025-06-16、2024-03-15 三个交易日，用 `data/raw/cffex/{date}.csv` 重新按分组聚合，与产物逐品种/逐机构比对多单、空单共 45 项全部一致；2024-03-15 的 `中信期货(经纪)`、`国泰君安(经纪)` 已分别归入 `citic`、`guotai`。

## Risks / Trade-offs

- **[中金所接口限频或临时不可用]** → 顺序单线程抓取、每请求 0.3–0.5 秒间隔、失败退避重试 3 次、已成功日期立即落盘，重跑只补缺口；失败日不写空缓存。
- **[会员更名/并购导致口径漂移]**（如"国投安信"等历史名称） → 映射表集中维护，新增别名需在变更记录中留痕；未识别名称自动落入 `others` 并在 `quality.unmatched_members` 中输出，便于发现新主体。
- **[手数加总口径易被误读为规模]** → 产物 `aggregation_note` + 页面常驻披露 + 分品种视图并列；图表 Y 轴标注"手"。
- **[同向率被误读为预测能力]** → 页面常驻"事后统计"声明、禁用建议式文案、每个比率强制带样本量与区间；spec 已将该约束写成 MUST。
- **[次日字段被误用进信号计算]** → 字段统一以 `next_` 前缀命名，`confirmed_*` 模块不导入机构模块；构建时不做任何回写指数产物。
- **[产物体积]** → 紧凑数组结构（预计 0.6–1.2MB），远超 snapshot 但独立端点按参数切片返回。
- **[与并行变更 `sector-trends-page` 的 `package.json` 冲突]** → 先检测 `echarts` 是否已存在再写入，避免重复键。

## Migration Plan

1. 新增流水线模块与产物文件，运行一次全量抓取，校验守恒断言与同向率统计；`data/index-signals.json`、`data/sector-trends.json` 保持不变（用 `compare_snapshot.py` 回归确认）。
2. 新增服务端模块与路由，验证 `data_state`/`stale` 分支与参数校验。
3. 新增前端页面与路由，构建通过后接入。
4. 回滚：删除 `/institutions` 路由与导航项、移除端点、删除产物文件即可；不影响任何既有页面与信号。

## Open Questions

- 是否需要"名义金额加权"视图（按 IF/IH 300 元/点、IC/IM 200 元/点折算为名义持仓），以便跨品种比较规模？可在不改变现有结构的前提下追加一个 `variety=notional` 切片，留待后续确认。
- 是否需要把"机构持仓"加入 `/api/snapshot` 首屏摘要（当前只做端点级摘要，页面进入时再拉取）。
