# Proposal

## Why

站点目前只覆盖"指数自身"与"板块"两个维度，缺少**资金立场**视角：用户无法回答"国泰君安、中信期货这些机构昨天在股指期货上是加多还是加空，这个动作和今天大盘的涨跌是同向还是对冲"。中金所每日公布的前 20 会员持仓排名（持买单量 / 持卖单量）是唯一公开、可回溯、口径稳定的机构持仓数据，但原始数据是"按合约 × 会员"的明细，既没有净持仓与日变化，也没有与次日指数走势的对照，直接看无法得出结论。

本次变更新增"机构多空单"页面，把中金所持仓排名加工成"每家机构每日净持仓 + 日变化 + 次日沪深300 同向/对冲判定"的序列，以列表与折线图呈现，并把国泰君安、中信期货单列、其余机构合并。

## 当前进展（2026-11-02 核实）

- **已完成**：数据抓取流水线、会员归一聚合、产物校验与原子写入、收盘刷新挂载、README、服务端接口与快照摘要；全量产物已构建（728 个交易日，2023-10-30 → 2026-10-30，`missing_dates=0`）。`pipeline` 24 个测试全绿。
- **遗留缺陷（本次修复）**：
  1. `InstitutionEndpointTest` 两个用例失败（测试直接调用端点函数，`Query` 默认对象导致 `validate_params` 抛 `TypeError`）；mtime 缓存与 stale 分支无测试覆盖。
  2. **数据口径**：2024-02-26 至 2024-04-26 共 43 个交易日，源数据会员名带"(经纪)"后缀（`中信期货(经纪)`/`国泰君安(经纪)`），未命中精确映射而落入"其他机构"（2026-11-02 用户确认：补别名并用本地缓存重建，不重抓远端）。
- **前端整体重做**：现有 `InstitutionPositions.tsx` + `institutionClient.ts` 为 mock 实现（浏览器直连中金所跨域必败、硬编码假数据与假准确率、手绘 canvas、无品种切换、路由为 `/institution`），违反"禁止 mock 替代真实数据"契约，本次废弃并按既有 spec 重建。

## What Changes

- **新增机构持仓数据流水线**：以 akshare `get_cffex_rank_table` 逐交易日抓取中金所股指期货（IF/IC/IH/IM）前 20 会员持仓排名，按会员汇总多单（持买单量）、空单（持卖单量），计算净持仓 `net = long - short` 与日变化 `Δnet`；原始日度明细落盘缓存，加工结果写入 `data/institution-positions.json`。
- **新增机构分组口径**：国泰君安、中信期货各自单列，其余前 20 会员合计为"其他机构"；同一会员的别名（如"国泰君安"与"国泰君安期货"）必须归一后再聚合。
- **新增品种维度**：提供"四品种汇总（手数加总）"与 IF / IC / IH / IM 分品种两套视图，汇总口径须显式声明为手数简单加总、非名义金额加权。
- **新增同向/对冲判定**：以 `sign(Δnet_t)` 与次日沪深300 涨跌方向 `sign(close_{t+1}/close_t - 1)` 比较——方向相同为"同向"，相反为"对冲"，`Δnet = 0` 或次日数据缺失为"待判定/未判定"；按机构 × 品种统计同向率，并同时给出已判定样本数与统计区间。
- **新增机构持仓页** `/institutions`：机构三行（国泰君安 / 中信期货 / 其他机构）的当日快照表、历史明细列表（可按日期、机构、品种筛选）、净持仓与次日涨跌的双轴折线图，以及同向率摘要（含样本量）。
- **新增只读接口** `GET /api/institutions/positions`：返回摘要 + 序列 + 分品种切片，`/api/snapshot` 保持精简、不承载长序列。
- **事后评估与预测严格隔离（关键约束）**：次日涨跌只能用于**事后统计与展示**，MUST NOT 进入任何顶部/底部信号计算，MUST NOT 以"预测"语义呈现；页面必须区分"已判定"与"待判定"，不得把尚未发生的次日结果当作当日已知信息展示。
- **BREAKING**：无。新增数据文件与端点，现有 `data/index-signals.json`、`data/sector-trends.json` 与全部既有端点字段不变。

## Capabilities

### New Capabilities

- `institution-position-view`: 机构多空单页面的行为契约，涵盖机构分组与别名归一、品种维度与汇总口径声明、当日快照表与历史明细列表、折线图内容与降级、同向/对冲判定语义与统计披露（样本量 + 区间 + 待判定处理）、事后评估不得预测化的展示约束、空状态与数据陈旧处理。

### Modified Capabilities

- `market-data-pipeline`: 新增"机构持仓产物契约"要求——抓取源与回溯区间固定、会员别名归一、多空单与净持仓定义、品种汇总口径、次日同向判定与同向率统计口径、原始明细缓存与幂等重抓、事后评估字段不得回流至信号计算。
- `live-data-api`: 新增"机构持仓查询端点"要求——摘要与序列字段完整性、机构/品种/日期区间参数与校验、未知参数 400、数据缺失与陈旧降级沿用既有语义。

## Impact

**受影响代码**

- 新增 `pipeline/institution_positions.py`（抓取、别名归一、聚合、净持仓与同向判定、产物组装）、`pipeline/test_institution_positions.py`。
- 修改 `pipeline/schemas.py`（新增 `institution-positions.json` 的 JSON Schema 与校验入口）、`pipeline/bootstrap.py`（收盘刷新链路中追加机构持仓更新）、`pipeline/io_utils.py`（复用 `protected_write` 原子写入，如需新增产物名则扩展）。
- 修改 `server/paths.py`（新增 `INSTITUTION_POSITIONS` 路径）、`server/snapshot.py` 或新增 `server/institutions.py`（读取产物、按参数切片）、`server/app.py`（新增路由）、`server/test_server.py`（端点契约用例）。
- 重写 `signal-hub-web/src/pages/InstitutionPositions.tsx`（原地替换 mock 实现）、新增 `src/components/InstitutionPositionChart.tsx`、`src/components/InstitutionSummaryTable.tsx`、`src/hooks/useInstitutionPositions.ts`。
- 删除 `signal-hub-web/src/data/institutionClient.ts`（浏览器直连中金所 + mock 回退的实现，废弃）。
- 修改 `signal-hub-web/src/App.tsx`（路由 `/institution` 更正为 `/institutions`）、`src/components/Sidebar.tsx`（导航指向新路由）、`src/data/types.ts`、`src/data/client.ts`。
- 修改 `pipeline/institution_positions.py`（`MEMBER_ALIASES` 追加 `中信期货(经纪)`/`国泰君安(经纪)` 两条精确别名）、`pipeline/test_institution_positions.py`（别名用例）；用本地缓存重建 `data/institution-positions.json`（不重抓远端）。
- 修改 `server/test_server.py`（`InstitutionEndpointTest` 改经 `TestClient` 并补缓存/stale 用例）、`server/institutions.py`（`validate_params` 类型健壮性）。
- 新增 `data/institution-positions.json`；`data/raw/cffex/` 为抓取缓存，与 `data/raw/` 同策略不入库。

**依赖与顺序**

- 数据层依赖已归档的 `integrate-live-market-data`：`pipeline/`、`server/`、`data/` 与 `/api/snapshot` 均可用，本变更在其之上做增量扩展。
- 前端图表依赖：本变更需要折线图（双轴：净持仓 + 沪深300 涨跌幅）。`sector-trends-page` 变更已计划引入 `echarts` 但尚未实施；若该变更先行落地则复用其依赖与封装风格，否则由本变更引入 `echarts`（路由级懒加载）。两条变更同时实施时需协调 `package.json` 的依赖写入，避免重复或冲突。
- 抓取耗时：近 3 年约 730 个交易日，实测四品种单日抓取约 0.4 秒，首次全量预计 5–10 分钟（含重试退避）；后续按交易日增量抓取，已缓存日期不重复请求。

**数据与口径影响（对应 AGENTS.md 第 1、4、5、7、8、9 条）**

1. **不引入未来函数**：次日涨跌仅在 `t+1` 收盘后回填，页面按"已判定 / 待判定"区分；该字段 MUST NOT 被任何信号算法读取，仅用于描述性统计（AGENTS 第 1 条）。
2. **不影响现有信号**：本次为纯新增产物与页面，`confirmed_index_signals`、`confirmed_sector_trends` 的输入输出不变；指数日线仍复用既有 `data/raw/indices-ths/sh000300.csv`，不改数据源与复权口径（AGENTS 第 4、5 条）。
3. **同向率必须带样本量**：页面与接口的每个同向率均须同时给出已判定样本数、统计起止区间；"待判定"不计入分母（AGENTS 第 8 条）。
4. **口径限制须显式披露**：四品种汇总为手数简单加总，不同品种合约乘数不同（IF/IH 300 元/点、IC/IM 200 元/点），汇总值不可用于跨品种规模比较；持仓排名为前 20 会员，非全市场持仓；这些限制必须在页面与接口字段说明中标注。
5. **描述性而非预测性**：本页展示的是"机构持仓动作与次日走势的历史对照关系"，不构成交易信号，页面不得渲染为买卖建议（AGENTS 第 2、7 条）。
