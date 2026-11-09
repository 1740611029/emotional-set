# Design

## Context

现状与动机见 `proposal.md`。这里只记录影响实现方式的事实与约束。

- 数据抓取集中在 `pipeline/fetch_market.py`（265 行，负责 7 个指数 + 31 个东财行业板块），已有 `call_with_retry`、`save_csv`、`sector_request_start` 等可复用件；增量合并已确立"起点含尾日 + 逐值比对覆盖"的自愈范式。
- 增量编排在 `pipeline/bootstrap.py`：`ensure_fresh_data()` 有三个早退分支（首次全量 / 已新鲜 / 当日已标记补齐），`latest_local_dates()` 只返回 `indices`/`sectors`/`institutions` 三条线，`data_gap()` 与 `pipeline/test_trading_calendar.py`、`test_refresh_and_rebuild.py` 的断言都以这三条线为准。
- 构建产物经 `pipeline/io_utils.py::protected_write` 落盘，schema 注册在 `pipeline/schemas.py::SCHEMAS`（`index-signals.json`、`sector-trends.json`、`institution-positions.json`），校验失败会抛错并保留上一版。
- 服务端 `server/app.py` 用 `SnapshotStore` / `PayloadStore` 读已落盘 JSON 组装响应，无数据库、无运行时计算。
- 前端指数卡走 `signal-hub-web/src/lib/indexSignalCharts.ts` 纯函数 + `IndexSignalChartCard.tsx` 的单个 `line` series，信号标记统一放在该 series 的 `markPoint` 里；前端测试用 `node --experimental-strip-types --test` 直跑 TS 纯函数。
- 已实测结论：核心 6 只区间内均破百亿；宽基全集共 10 只破百亿；核心 6 只同日破百亿 51 个交易日；6 只全部同日破百亿仅 2024-09-30 与 2026-11-03 两次。

## Goals / Non-Goals

**Goals:**

- 让"哪几只 ETF 破百亿""是否只有 6 只""放量是否对应底部/上升趋势"三个问题各自带有可复核证据与样本量。
- 让前端指数卡上的放量标记与研究报告中统计的事件来自**同一份识别逻辑**，避免图上一个样、报告里另一个样。
- 新增数据线对既有三条数据线与全部信号计算结果保持零影响。

**Non-Goals:**

- 不把 ETF 放量做进顶底信号的三层确认，也不做进板块强度。本轮只做观测与统计；是否作为因子采纳属于下一轮独立变更。
- 不做 ETF 放量事件的独立列表页面或详情页，只在指数信号卡上标注。
- 不做盘中实时 ETF 成交额（本轮只做日频收盘口径）。

## Decisions

### D1：新建 `pipeline/fetch_etf.py`，不并入 `fetch_market.py`

`fetch_market.py` 已承载两条数据线且体量大；ETF 线需要独立的清单校验、独立的失败隔离语义（失败不阻塞），并入会让两件事纠缠。新模块从 `fetch_market` 导入 `call_with_retry`、`save_csv`，从 `trading_calendar` 导入 `last_completed_trading_day`，保持单一数据源实现。

替代方案：并入 `fetch_market.py` 并新增 `--target etf`。放弃原因：ETF 线与其他线失败语义不同，混在一个模块里容易被后来的改动顺手改成"失败即抛错"。

### D2：识别与统计共用一套纯函数模块

新增 `pipeline/etf_volume_events.py`，只放无 I/O 的纯函数：日级命中计算、级别判定、放量簇合并、市场状态分类、前瞻收益统计、Wilson 区间。构建流程（生产事件）与研究脚本都调用它。

2026-11-06 用户确认修正事件簇分级：`events.level` 固定为簇首日当天的 `days.level`，不得用簇内最高级回标；版本升为 `etf-volume-spike-v1.1`。首日日期、级别、状态均在当日可知。簇结束日、天数、峰值仅作截至输入末日的事后描述，不参与首日收益分层。图表逐日标记保持原规则，日级 L3 次数不等于首日为 L3 的簇样本数。旧报告保留，修正报告以版本化文件名另存，不改阈值、数据源或既有信号算法。

### D3：事件产物独立成 `data/etf-volume-events.json`

不塞进 `data/index-signals.json` 的每个指数条目。理由：该文件 schema 严格要求 7 个指数且带 `evaluation` 校验，全市场事件与指数无关，内嵌会让同一份事件重复 7 份、放大体积并污染既有校验路径。

结构：

```json
{
  "as_of": "2026-11-04",
  "generated_at": "...",
  "threshold_amount": 10000000000,
  "cluster_gap_days": 2,
  "mode_basis_index": "sh000300",
  "pool": [{"code": "510300", "name": "沪深300ETF华泰柏瑞", "index": "沪深300"}],
  "days": [{"date": "2026-11-03", "level": "L3", "etf_codes": ["510300"], "total_amount": 1.2e10, "state": "趋势上行区"}],
  "events": [{"start_date": "...", "end_date": "...", "days": 4, "level": "L3", "state": "趋势上行区", "peak_amount": 1.5e10}]
}
```

`days` 供前端按日打标记，`events` 供报告按簇首日固定级别统计；二者为同一日级识别结果的不同粒度，不能把事件簇首日级别和簇内各日级别混用。

### D4：市场状态以沪深300 为唯一基准指数

核心池横跨 300/50/500/1000/创业板/科创50，没有任何一个指数代表全部；用 7 个指数投票会让结论难以解释、样本被摊薄。取沪深300（`sh000300`，项目已注册且有 6 年日线）作为唯一状态基准，并在产物与报告中显式写明。前瞻收益仍按 7 个已注册指数分别报告，二者不冲突。

替代方案：用上证指数。放弃原因：编制口径与核心池跟踪的宽基不一致，且 300 更贴合机构基准。

2026-11-06 用户确认补齐指标预热历史并另存 `etf-volume-spike-v1.2`：研究入口保留所选终点及之前的完整指数历史，MA120 与 250 日高点仍只读事件日及之前的数据；ETF 历史同步保留用于确定真实簇首日，避免研究起点落在簇中途时制造新事件。事件、两类基线、名单日期与时间分段仍限定在 `--start`～`--end`，收益评价不使用 `--end` 之后的数据。不改冻结参数、数据源或顶底信号，v1.0/v1.1 报告原件保留；在同一当前输入上分别复刻短历史最高级、短历史首日级别和完整预热首日级别，用以区分两次修正的影响。研究事件的首日日期、级别、状态须与正式构建入口一致。

### D5：参数全部预注册，并按时间切分前后两段各自报告

规则要求调参与验证分离。本轮阈值来自强约定（100 亿元整数关口）而非拟合，因此不存在"调参段"；但仍按时间把区间切成前段与后段，两段各自给完整统计，避免"看了全样本才决定怎么切"。冻结参数：

| 参数 | 冻结值 | 出处 |
| --- | --- | --- |
| 放量阈值 | 100 亿元（1e10 元） | 用户口径与新闻通行整数关口 |
| 普通放量基线带 | 50 亿 ~ 100 亿元 | 为使基线同量级而设 |
| 簇合并间隔 | 2 个交易日 | 覆盖周末与单日断档，避免把一轮行情拆成多个事件 |
| 状态指数 | 沪深300 | D4 |
| 趋势均线窗口 | 120 日 | 半年级趋势，避免与 250 日窗口共线 |
| 回撤观察窗口 | 250 日 | 一年高点 |
| 底部区阈值 | 距 250 日高点回撤 ≥ 15% 且收盘 ≤ MA120 | 预先声明 |
| 上行区阈值 | 距 250 日高点回撤 < 8% 且收盘 > MA120 | 预先声明 |
| 前瞻期 | 1 / 3 / 5 / 10 / 20 交易日 | 与现有信号评价窗口对齐 |
| 单层样本下限 | 8 个事件；`L3` 单独下结论需 ≥ 3 次 | 低于此只标"样本不足" |
| 研究区间 | 2024-09-24 ~ 最近已完成交易日 | 924 起点，此前份额基数不可比 |

若后续要调整任一参数，必须新建一轮研究、保留本轮结论作为训练段结果，不得就地改阈值后重报。

### D6：接入刷新流程用"包装函数"而不是改早退分支

`ensure_fresh_data()` 内部改名 `_ensure_fresh_data_core()`，新 `ensure_fresh_data()` 调 core 拿到结果后，再 best-effort 执行 ETF 增量补齐并把结果并入返回字典的 `refreshed.etf`。这样既有三个早退分支一行不改，`latest_local_dates()` 保持三条线，`data_gap()` 语义不变，`test_trading_calendar.py` / `test_refresh_and_rebuild.py` 的既有断言全部继续有效。

替代方案：在 `refresh_and_rebuild.main()` 与 `server/scheduler.py` 各调一次。放弃原因：两处都要改且日后新增调用方容易漏，包装法对所有调用方天然生效。

### D7：服务端读取走独立轻量加载，损坏即降级为空数组

`server/snapshot.py` 已有 `PayloadStore` 承担"读 JSON + 缓存"职责。事件产物若能复用该模式就复用，否则新增同形态的读取 helper：按 mtime 失效、解析失败返回 `None`，接口层统一降级为 `[]`。绝不因为研究产物损坏把 `/api/snapshot` 打成 500。

### D8：前端在同一 series 的 `markPoint` 内扩展，不新增 series

现有信号标记放在 `line` series 的 `markPoint.data` 里。ETF 放量标记追加到同一数组，用 `symbol: 'circle'`、`coord: [date, 可见区间最低收盘 × 0.97]` 落到曲线下方，从而与三角信号标记在位置和形状上都不冲突；级别用尺寸（6 / 8 / 11）与描边宽度（1 / 2 / 2）区分，满足"不依赖颜色区分"的要求。颜色取 `--color-brand-500`，不引入新主色。

tooltip 的 formatter 已按日期匹配 marker，扩展为先查信号 marker、再查事件 map，两段都命中时顺序拼接，天然满足"互不覆盖"。

### D9：ETF 日线与既有 `etf-shares/`、`etf-nav/` 分目录

三个目录语义不同（份额、净值、日线成交额），不混放。`etf-daily/` 与 `sectors-em-industry/` 同级同构（一标的一 CSV + manifest），便于沿用既有读取与自检习惯。

## Risks / Trade-offs

- [东财接口字段或单位变化] → `fetch_etf` 显式校验七个字段存在且 `amount` 为数值，缺字段直接报错并保留上一版 CSV，不写半截数据。
- [ETF 拆分/合并造成成交额跳变] → 核心 6 只近期无拆分记录；manifest 记录每只起止日期与行数，单日行数或成交额出现数量级异常时在报告与日志中告警。
- [事件样本量小导致结论不稳] → 已预注册"单层 n ≥ 8、L3 ≥ 3"下限，不足即标"样本不足，暂不能判断"；L3 仅两次，报告中只能作为个例描述，不得当胜率依据。
- [刷新时长增加] → 6 次请求约 +3 秒，且集中在一个 best-effort 阶段；任一失败只告警。
- [快照体积增加] → 51 个放量日 × 约 150 字节 ≈ 8KB，相对 `sector-trends.json` 可忽略。
- [跨年份不可比] → 2024 年前 ETF 份额基数小，同一 100 亿阈值含义不同；研究区间固定在 924 之后，跨年份只作定性提示。
- [未来函数风险] → 事件日、首日级别与首日市场状态只取当日及之前数据；前瞻收益仅用于事后评价，不回流识别。测试必须覆盖簇尚未结束时截断、后续升级及篡改后置数据，断言已出现事件的首日属性不变。

## Migration Plan

1. 备份：复制 `data/index-signals.json`、`data/sector-trends.json` 到 `.backup/2026-11-05-etf-volume-spike/`。
2. 先在临时目录演练：`python -m pipeline.build_dashboard --output-dir .tmp-drill` 确认新增产物与既有产物均能生成。
3. 运行 `pipeline/run_etf_volume_study.py` 生成研究报告（只写 `data/research/`）。
4. 正式重建并逐条比对 `index-signals.json` / `sector-trends.json` 与备份一致。
5. 回滚：`git revert` 本次提交 + 删除 `data/raw/etf-daily/`、`data/etf-volume-events.json`、`data/research/etf-volume-spike.*`；因接口层已定义空数组降级，前端在产物缺失时自动只显示原有信号标记。
