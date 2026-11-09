# Proposal

## Why

站点当前只有"宽基指数"一个市场维度，缺少 A 股最常用的板块视角：用户无法回答"今天哪些板块在走多头、哪些在退潮"。`pipeline/confirmed_sector_trends.py` 已跑通板块四态算法 `sector-donchian20-ma60-v1.0`（31 个申万一级 + 131 个二级，最新 `as_of` 2026-10-31，分布 bull 4 / bear 25 / rise 2），但产物 `data/sector-trends.json` 只有一份扁平分组，既无页面承载，也不含任何价格序列，无法"看走势"。

本次变更新增板块趋势浏览页，把已有四态判定结果可视化，补齐"走势"所需的板块历史序列契约，并把二级板块从"继承父级状态"改为**独立判定**。四态判据本身不动，只改变二级板块的状态来源。

## What Changes

- **新增板块趋势页** `/sectors`：左侧一级板块列表（四态筛选、强度排序、名称/代码检索），右侧选中板块的走势图 + 状态摘要 + 二级细分板块表。二级板块**独立判定四态**，与父级不一致时显式提示。
- **二级板块改为独立四态判定（算法变更，需留痕）**：取消 `calculate_sector_universe()` 中"二级板块 `state`/`days` 直接赋值父级"的逻辑，改用二级板块自身日线独立计算。实测影响：131 个二级板块全部具备足够历史（最少 171 个交易日），独立判定后 **21 个与父级状态不同**（110 个一致），分布由"完全继承父级"变为 bear 114 / bull 11 / rise 5 / warn 1。
- **强度口径可解释化**：产物新增 `strength_parts`（`range_position` 区间位置分、`ma_score` MA60 偏离分）与 `strength_basis`（`directional` / `inverted`），页面可展开显示"强度由哪两项算出、是否取反"，并明确 0–100 的含义与"跨状态不可比"的约束。
- **新增二级板块独立状态评估**：输出二级板块四态各自的 10/20 日方向胜率，含触发数、已判定样本、成功数、Wilson 95% 区间与分段。实测结果（2020-10-30~2024-12-31 研究段 / 2025-01-01 起验证段）显示**方向胜率在两段之间符号翻转**（bull 20 日 46.1% → 51.8%，rise 48.0% → 59.8%，bear 58.4% → 47.0%），无稳定优势，因此二级四态只能作描述性分类，页面必须披露该结论。
- **引入 ECharts**：新增 `echarts` 依赖，封装 `<SectorTrendChart>`，绘制收盘折线 + MA60 + 四态背景色带 + 十字光标 + 时间范围切换。配色从 `index.css` 的 CSS 变量读取，与现有设计系统一致。
- **板块趋势产物扩展**：`data/sector-trends.json` 每个板块新增 `date`、`state`、`data_anomaly`、`strength_parts`、`strength_basis`、最近 250 日 `close`/`state` 序列与 30 点 `spark`；二级板块条目补充 `code`（当前只有 `name`）。日期序列改为文件级共享 `dates` 数组 + 单字母状态码，控制体积。
- **新增板块历史接口**：`GET /api/sectors/{code}/history?days=60|120|250`，与既有 `/api/indices/{code}/history` 同构；`/api/snapshot` 的 `sectors` 条目补齐上述摘要字段（不含长序列）。
- **前端数据层扩展**：`src/data/types.ts` 的 `SectorItem` / 子板块类型补齐字段，`src/data/client.ts` 新增 `fetchSectorHistory`；新增 `useSectorHistory` 复用既有降级逻辑，板块摘要直接消费 `MarketDataProvider` 快照。
- **四态判据本身不变**：`bull` / `warn` / `bear` / `rise` 的判据（唐奇安 20 中轨、MA60）、`strength` 公式与 `days` 计数保持 `sector-donchian20-ma60-v1.0` 原样。改变的只是**二级板块的状态来源**：由"继承父级"改为"用自身日线独立计算"。
- **BREAKING**：无页面级破坏。现有页面尚未消费 `sectors`，字段全部为新增；但**数据层面二级板块的 `state`/`days` 会变化**（21/131），依赖旧继承口径的读法需同步调整。

## Capabilities

### New Capabilities

- `sector-trend-explorer`: 板块趋势浏览页的行为契约，涵盖一级/二级板块组织、四态语义与标签、二级独立判定与父级差异提示、**强度数值语义与构成解释**、筛选与排序、走势图内容与降级、空状态与数据异常展示。

### Modified Capabilities

- `market-data-pipeline`: 新增"板块趋势产物契约"要求——四态取值与判定口径固定、`strength`/`days` 定义、**二级板块独立判定**、强度构成导出、每板块导出最近 250 日序列、二级独立状态的分状态准确率评估，以及"一级判定结果不变、二级变化可留痕"的验收约束。
- `live-data-api`: 新增"快照板块条目字段"与"板块历史查询"要求——字段完整性（含强度构成）、单板块历史端点、未知代码 400/404 与历史不足的响应约定、陈旧降级沿用。

## Impact

**受影响代码**

- 新增 `signal-hub-web/src/pages/Sectors.tsx`、`src/components/SectorTrendChart.tsx`、`src/components/SectorStateBadge.tsx`、`src/hooks/useSectorTrends.ts`、`src/hooks/useSectorHistory.ts`。
- 修改 `signal-hub-web/src/App.tsx`（`/sectors` 路由）、`src/components/Sidebar.tsx`（"板块"分组）、`src/components/icons.tsx`（板块图标）、`src/data/types.ts`、`src/data/client.ts`、`package.json`（`echarts`）。
- 修改 `pipeline/confirmed_sector_trends.py`（二级板块改为独立判定、新增历史序列与强度构成导出、新增二级分状态评估）、`pipeline/build_dashboard.py`（`build_sectors()` 输出扩展）。
- 修改 `server/app.py`（新增板块历史路由）、`server/snapshot.py`（`_sector_block` 透传新字段、剔除长序列）。
- 修改 `data/sector-trends.json`（重新生成，体积从 24KB 增至约 390KB）；`data/index-signals.json` 不受影响。

**依赖与顺序**

- 数据层依赖 `integrate-live-market-data` 已落地并归档：`src/data/{types,client,MarketDataProvider}`、`pipeline/`、`server/`、`data/` 均已存在，`/api/snapshot` 可用。本变更在其之上做增量扩展，**不再需要静态兜底或离线生成阶段**。
- 前端新增运行时依赖：`echarts`（约 300KB，gzip 后约 100KB），按需引入 + 路由级懒加载控制首屏影响。

**数据与口径影响（对应 AGENTS.md 第 4、5、9 条，需在评审时确认）**

1. **一级判定结果不变**：一级板块的 `state`/`strength`/`days` 与分组归属必须逐条不变；**二级板块的 `state`/`days` 会变化**（21/131），这是本次算法变更的预期结果，必须以变化清单形式留痕（AGENTS 第 9 条）。`strength` 数值不因本次改动而变化——二级强度本就按自身状态取反计算。
2. **二级板块独立四态无稳定方向优势**：研究段（2020-10-30~2024-12-31）与验证段（2025-01-01 起）的 20 日方向胜率符号相反（bull 46.1% → 51.8%，rise 48.0% → 59.8%，bear 58.4% → 47.0%，warn 55.4% → 53.2%），样本量分别为 4350/3890/4643/3580 与 2471/1887/2339/2081。因此二级四态只能作为**描述性分类**展示，页面必须披露该评估结果与样本量，不得暗示预测能力（AGENTS 第 2、7、8 条）。
3. **强度是"状态内方向强度"，不是涨跌预期**：0–100，50 为中性；`bull`/`rise` 未取反，`bear`/`warn` 取反（`100 - 基础分`）。因此**跨状态的强度数值不可直接比较**，页面必须给出该约束与构成解释。
4. **数据源与复权口径不变**：仍为 akshare 申万行业日线，沿用现状口径。
5. **板块为描述性分类**：`evaluation.note` 已声明"方向胜率不代表交易收益"。页面不得把四态渲染成买卖建议，且须保留该提示与空头状态的历史胜率说明。
