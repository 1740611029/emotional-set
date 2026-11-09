# Proposal

## Why

用户看到一个说法：924 行情以来有 6 只宽基 ETF 单日成交破百亿，且"每次 ETF 连续放量都出现在下跌底部拐点反弹或上升趋势中"。这两个问题本项目现在**无法回答**：`data/raw/` 只有 ETF 份额（`etf-shares/`）、净值（`etf-nav/`）和一份中证月度快照（`etf-spike.json`），没有 ETF 日线成交额，也没有任何 ETF 抓取或研究脚本；前端的指数信号卡也无法叠加 ETF 放量标记。

ETF 成交额是"真实资金进场"的直接可观测证据，与现有六维因子（价格、动量、量能、宽度等）来源不同。若经统计检验确实对顶底拐点有增量信息，它就是一个独立的对照维度；若检验后发现只是行情结果的伴生现象，也应该用数据把结论固定下来，避免以后反复凭印象判断。

本次调查已用东财 `fund_etf_hist_em`（不复权日线，成交额单位元）实测 2024-09-24 ~ 2026-11-04 区间，先核实了用户提到的说法：

- 用户给出的 6 只（588000 科创50ETF、159915 创业板ETF、510300 沪深300ETF、510050 上证50ETF、512100 中证1000ETF、510500 中证500ETF）区间内均出现过单日成交额 ≥ 100 亿元，**名单成立**。
- 但"只有 6 只"**不成立**：同口径下宽基范围内还有 588080（科创50ETF 易方达）、159919（沪深300ETF 嘉实）、510310（沪深300ETF 易方达）、510330（沪深300ETF 华夏）也曾破百亿，共 10 只。需要把"核心 6 只"与"宽基全集"两个口径分开表述。
- 核心 6 只同日破百亿的交易日共 51 个；**6 只全部同日破百亿只出现两次：2024-09-30 与 2026-11-03**。近几日（2026-11-03）确实符合用户描述，且是 924 行情以来第二次。
- 这些放量日合并成连续放量簇后约十余个事件，量级够做分层统计，但**不足以支撑阈值寻优**，因此阈值必须预先声明而不能拟合。

## What Changes

- 新增 ETF 日线成交额数据线：东财 `fund_etf_hist_em`（不复权、成交额单位元），落盘 `data/raw/etf-daily/<code>.csv` + `etf-daily-manifest.json`，字段与板块 CSV 同构（`date/open/close/high/low/volume/amount`）。核心池固定为用户核实过的 6 只，每日校验代码与名称；不做动态名单，因为换名单等于换一套历史。
- 增量抓取接入每日刷新流程：追加在 `ensure_fresh_data()` 主体之后做 best-effort 补齐，**不进入 `latest_local_dates()` 的三条 stale 判定线**，失败只告警不阻塞重建。
- 新增生产产物 `data/etf-volume-events.json`：放量事件清单（日期、级别、命中 ETF、合计成交额），由 `build_dashboard` 生成并经 `schemas` 校验，纳入 `protected_write` 契约。
- 新增研究脚本 `pipeline/run_etf_volume_study.py`：产出 `data/research/etf-volume-spike.json` + `.md`，回答三件事——破百亿的到底是哪几只（核心池 vs 宽基全集）、"只有 6 只"是否成立、放量簇之后的指数表现是否真的偏向底部拐点或上升趋势。
- 研究结论必须带基线对照：把"放量簇之后 N 日收益"与"同状态下非放量日"、"50~100 亿普通放量日"对照，避免把"行情本身在涨"误判成"放量预示上涨"；每个结论强制附样本量、统计区间与 Wilson 95% 区间。
- API 在 `/api/snapshot` 顶层与 `/api/indices/{code}/history` 返回 `etf_volume_events`（纯增量字段，不改既有字段语义）。
- 前端指数信号卡在现有 ECharts 折线上叠加 ETF 放量标记与图例，配色沿用现有色板，不新增主色。
- **不改动任何顶部/底部信号计算**：改动前后 `data/index-signals.json`、`data/sector-trends.json` 必须逐条一致。

## Capabilities

### New Capabilities
- `etf-volume-events`: ETF 放量事件的定义、分级、市场状态分类、分层统计与研究结论契约（含无未来函数自检、样本量与区间强制披露、研究与生产隔离）

### Modified Capabilities
- `market-data-pipeline`: 新增一条 ETF 日线成交额数据线（数据源与复权口径固定、增量合并与逐值校验、manifest、失败不阻塞既有数据线）
- `live-data-api`: 快照端点与指数历史端点新增 `etf_volume_events` 字段
- `live-data-ui`: 指数信号卡图表新增 ETF 放量标记、图例与 tooltip 说明

## Impact

新增文件：

- `pipeline/fetch_etf.py`（ETF 日线抓取与增量合并、清单校验、manifest）
- `pipeline/etf_volume_events.py`（事件识别、分级、市场状态分类、分层统计，纯函数，供构建与研究共用）
- `pipeline/run_etf_volume_study.py`（研究入口，产出 `data/research/etf-volume-spike.{json,md}`）
- `pipeline/test_fetch_etf.py`、`pipeline/test_etf_volume_events.py`
- `data/raw/etf-daily/*.csv`（6 个文件）、`data/raw/etf-daily-manifest.json`、`data/etf-volume-events.json`
- `signal-hub-web/src/lib/etfVolumeMarkers.ts` 及 `signal-hub-web/tests/etfVolumeMarkers.test.ts`

修改文件：

- `pipeline/build_dashboard.py`：新增生成 `etf-volume-events.json` 的步骤与 `--validate` 覆盖
- `pipeline/schemas.py`：新增 `etf-volume-events.json` 的 schema 与展示校验
- `pipeline/bootstrap.py`：`ensure_fresh_data()` 末尾追加 ETF 增量补齐（不加入 `latest_local_dates()`）
- `server/snapshot.py`、`server/app.py`：快照与指数历史端点透出 `etf_volume_events`
- `signal-hub-web/src/lib/indexSignalCharts.ts`、`src/components/IndexSignalChartCard.tsx`、`src/data/types.ts`

影响点（按项目规则先行说明，请评审时确认）：

1. **不影响信号计算**：ETF 放量只用于展示标记与研究，不进入顶底信号的三层确认，不进入板块趋势强度；`index-signals.json` / `sector-trends.json` 逐条不变。apply 阶段会先备份这两个文件并做逐条比对。
2. **刷新流程新增网络请求**：每日刷新多 6 次东财请求（实测单只约 0.3~1s，整体约 +3s），失败只打印告警、不阻塞重建，也不写当日"已补齐"标记以外的任何状态。
3. **不改 stale 判定语义**：不把 ETF 加入 `latest_local_dates()`，因此"数据是否覆盖最近交易日"的判定仍只由指数/板块/机构三条线决定，现有 `test_trading_calendar.py`、`test_refresh_and_rebuild.py` 的既有断言保持有效。
4. **新增生产产物**：`data/etf-volume-events.json` 进入 `protected_write` 契约，schema 校验失败时保留上一版，不会写出半截文件。
5. **数据口径固定**：东财不复权日线、成交额单位元、历史起点 2000-01-01（实际可得以接口为准）。口径一旦确定不更换；若将来换源，等于换一套放量历史，必须单独立项并出对照报告。
6. **已知局限**：ETF 成交额含二级市场换手与套利资金，不等于净申购；2024 年以前份额基数小，早期放量阈值与当前不可直接比较，跨年份结论只在报告里作定性提示，不作为统计结论。
