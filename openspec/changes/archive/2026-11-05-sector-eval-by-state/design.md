# Design

## Context

动机见 [proposal.md](./proposal.md) 的 Why。实现层面的既有事实（已核对代码，非推测）：

- 评估入口是 `pipeline/confirmed_sector_trends.py::build_sector_evaluation()`：它把 31 个板块的状态切换事件汇总后，调 `research_algorithms.summarize_sector_events(events, [])` —— **第二个参数是分组键列表，当前传空列表，这就是「不拆四态」的唯一开关**。
- 事件本身（`research_algorithms.evaluate_sector_states()`）已带 `type`（四态）与 `horizon` 的 `outcome_{10,20}`，方向口径已固定为 `bull`/`rise` 期望上涨、`warn`/`bear` 期望下跌。拆分只是把同一批事件换个分组键，**不需要重算任何判据**。
- 旧的两层产物里 `subsector_evaluation.segments[*].states.<state>.<"10"|"20">` 就是这个形状（留痕于 `data/sector-trends.pre-signed-strength.json` 与 `.backup/2026-11-03-板块换源/sector-trends-换源前.json`），本次是把同样的形状补回板块级。
- 契约侧：`pipeline/schemas.py::SECTOR_EVALUATION` 要求 `segments[*].horizons`，且 `segments` 项对象**没有** `additionalProperties: false`，所以不做收紧的话旧结构照样能通过校验。
- 测试侧：`pipeline/test_io_utils.py::SectorContractTest` 直接读生产 `data/sector-trends.json`、`server/test_server.py` 的 `_sector_payload()` 也读生产文件，因此**契约收紧与生产产物重建必须同批完成**，否则测试必红。
- 前端侧：`SectorBlock.evaluation` 目前是 `unknown`，全仓无消费者（原先渲染 `subsector_evaluation` 的面板已随二级评估删除）；服务端 `snapshot._sector_block()` 原样透传，无需改动。

## Goals / Non-Goals

**Goals:**

- 产物 `evaluation` 以四态为唯一口径，且四态统计与拆分前的事件集合严格可对账（样本、条目、成功、未决分别求和相等）。
- 契约能拒绝旧结构，避免两套口径在产物里共存。
- 页面能把这份评估如实呈现：带样本量、带 Wilson 区间、小样本显式标记，不随选中板块变化。

**Non-Goals:**

- 不动四态判据、有向强度、持续天数、历史序列、行业宽度、指数顶底信号与候选状态。
- 不重跑调参或重新划分研究段/验证段（划分与阈值原样保留）。
- 不改 `subsector_evaluation` 的历史留痕产物（旧文件只读保留）。
- 不做评估数据的额外研究产出（如按板块或按年份细分）。

## Decisions

### 1. `states` 直接替代 `horizons`，不并存

`segments[*]` 从 `{"segment","independent","horizons"}` 改为 `{"segment","independent","states"}`，`states` 为 `{bull,warn,bear,rise}`，每态再分 `{"10","20"}`（用户选定）。

- 理由：两套口径并存时，读者容易把聚合值当主结论、把分态当补充，刚好与本次要解决的问题相反；且契约里同时存在 `horizons` 与 `states` 会让"哪个是准的"永远说不清。
- 已否掉的替代方案：①保留聚合 + 并列 `states`（用户明确否定）；②`states` 用数组 `[{state, horizons}]` 而非键式对象 —— 数组在 JSON Schema 与前端取值上都更啰嗦，且无法表达"某态无样本"的稳定形状。
- 信息无损：四态 `samples`/`entries` 之和恒等于原聚合值（同一批事件重新分组），需要总体口径时可由四态求和得到，README 里会写明这一点。

### 2. 复用 `summarize_sector_events` 的分组键，不新写统计

只把 `build_sector_evaluation()` 里的 `summarize_sector_events(all_events, [])` 改成 `(["type"])`，`build_segment()` 按 `type × horizon` 取行；`small_sample` 阈值（<30）、`wilson_interval`、未决不进入分母等全部沿用现有实现。

- 理由：另写一份分组统计必然引入第二套四舍五入/边界口径，和聚合值对不上时的排查成本远高于改一个参数；也让"拆分只改分组"这句话可被代码结构本身证明。
- 已否掉的替代方案：在 `build_sector_evaluation()` 里对 `all_events` 自己做 groupby —— 与既有函数重复。

### 3. 算法版本号保持 `sector-donchian20-ma60-v2.0-em-source`

`ALGORITHM_VERSION` 与 schema 里的 const 都不改。

- 理由：版本号标识**判定规则**（唐奇安 20 中轨 + MA60 的四态组合与统一有向强度），本次判定规则一行未改；升版号会误导下游以为四态历史变了，还要连带重跑换源对照留痕（AGENTS 第 5 条：换口径等于换一套信号历史，本次并没有换）。
- 留痕方式（对应 AGENTS 第 9 条）：变更前后各重建一次，断言除 `evaluation` 外产物逐条相等（`groups`/`dates`/`as_of`/`algorithm_version`/`data_source` 与每个板块的 `state`/`strength`/`days`/`series`），结果写入 tasks 的完成记录。
- 已否掉的替代方案：升为 `v2.1-eval-by-state` —— 会破坏"版本号＝判定口径"的既有语义。

### 4. 四态键齐备，无样本为 `null`

每个「段 × 窗口」必须出现四个状态键；某态在该窗口没有任何事件时，其 `{"10": null, "20": null}`。

- 理由：前端与 schema 需要稳定形状，"键缺失"和"没有样本"必须可区分；这也与既有 `horizons` 在无样本时输出 `null` 的语义一致，不引入新约定。

### 5. 契约收紧到能拒收旧结构

`schemas.py`：删除 `segments[*].properties.horizons`，新增 `SECTOR_STATE_HORIZONS = {type: object, required: ["10","20"], properties: {...}, additionalProperties: false}`；`states` 的四个键各为 `{"type": ["object","null"], ...}` 或直接复用 `SECTOR_STATE_HORIZONS` 加 null 联合；`segments[*]` 与 `states` 均加 `additionalProperties: false`。

- 理由：否则旧产物（带 `horizons`）能通过校验，"不得同时提供两套口径"这条需求就没有执行手段。
- 后果（已纳入 tasks 顺序）：收紧后必须立即重建生产 `data/sector-trends.json`，否则 `pipeline/test_io_utils.py::SectorContractTest` 与 `server/test_server.py` 会因读旧产物而失败。

### 6. 前端：纯函数 + 独立组件，挂在详情下方

- 新增 `src/lib/sectorEvaluation.ts`（纯函数，无 React 依赖）：把 `SectorEvaluation` 归一成「段 × 窗口」的行数据，提供比率/区间/样本量的格式化与"无样本/小样本"判定，便于 `node:test` 直跑。
- 新增 `src/components/SectorEvaluationPanel.tsx`：两段各一张卡（研究段 / 验证段），卡内四行（四态）× 两列（10 日 / 20 日），每格显示胜率 + `n=` + Wilson 区间 + 小样本标记；无样本显示 `—`。
- 位置：板块页 `xl:grid-cols-[320px_1fr]` 网格**下方**的全宽区块，不放进单板块详情卡，从布局上就避免"这是选中板块的胜率"的误读。
- 复用：`STATE_ORDER` / `STATE_LABELS` / `STATE_TEXT_CLASS`（与列表、图例同源）、现有 `Notice` 与淡色卡片样式（`glass-card`、`brand/mint/ocean/coral`、`tabular-nums`），不引入新依赖、不改主题。
- 已否掉的替代方案：①直接内联进 `Sectors.tsx`（已 360 行，且无法单测）；②做成可折叠抽屉 —— 评估是"验证披露"，默认可见才符合"说明文字 MUST 可见"的要求。

### 7. 排期与门禁：先演练、核对、再动生产

顺序固定为：改 pipeline + 单测 → `build_dashboard.py --output-dir <临时目录>` 演练并做"除 evaluation 外逐条相等 + `index-signals.json` 哈希不变"的比对 → 把四态数字提交用户核对 → 重建生产 `sector-trends.json` → 前端接入 → 文档与留痕。

- 理由：沿用换源变更 5.4 的"人工核对后才覆盖生产产物"门禁；本项目无 git，`.backup/` 是唯一回滚依据。
- 必须同时规避的坑：重建前确认没有常驻服务或旧代码进程在跑（2026-11-03 已发生过一次旧进程用旧代码覆盖生产产物），并且不要用 `pytest pipeline` 代替重建（全量测试虽已改为临时目录输出，但历史教训是"测试与常驻进程直写生产产物"）。

## Risks / Trade-offs

- [去掉聚合后看不到单一总体数字] → 四态样本之和等于原聚合样本，README 与页面说明写清可求和复算；页面不额外展示"合计"行（避免把聚合重新抬成主结论）。
- [分态后个别格子样本变小、置信区间变宽] → 沿用 `small_sample` 阈值的显式标记；不因拆分调整阈值（否则等于为了好看改口径）。
- [验证段 `rise`/`bull` 的高胜率被读成可交易信号] → 区块固定展示"描述性分类、不代表交易收益、验证段非独立验证"；产物 `research_status` 不变；页面禁止指令性措辞（已在 spec 场景中固化）。
- [契约收紧导致测试在重建前变红] → 任务顺序把"重建生产产物"排在"跑契约测试"之前，且中间不提交半成品状态。
- [旧进程覆盖生产产物] → 重建前核查服务进程；变更前后产物哈希留痕。
- [前端把 `evaluation` 当作可选字段而静默渲染空表] → 类型设为 `SectorEvaluation | null`，为空时渲染"评估尚未生成"提示（spec 场景）。

## Migration Plan

1. 备份 `data/sector-trends.json` 到 `.backup/2026-11-04-板块评估四态/`（沿用备份目录命名约定）。
2. 代码与测试就绪后，先 `--output-dir` 演练产物，核对四态数字与"其余字段逐条相等"，提交用户确认。
3. 用户确认后重建生产产物，再跑 `pytest pipeline server` 全量。
4. 回滚方式：用第 1 步的备份覆盖 `data/sector-trends.json`，并把 `schemas.py` 与前端回退到变更前（无 git，备份目录即回滚依据）。
