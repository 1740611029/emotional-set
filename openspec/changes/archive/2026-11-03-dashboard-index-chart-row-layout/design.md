# Design

## Context

【指标总览】图表主区当前实现（`signal-hub-web/src/pages/Dashboard.tsx` L170-181）：`grid grid-cols-1 md:grid-cols-2 gap-6` 容器直接按 `snapshot.indices` 顺序渲染 7 张 `IndexSignalChartCard`，仅第 7 张（`index === 6`）加 `md:col-span-2`。`snapshot.indices` 的顺序由数据端 `pipeline/fetch_market.py` 的 `INDEXES` 字典决定：上证指数、深证成指、创业板指、科创50、沪深300、中证500、中证1000。`IndexSignalChartCard` 已支持外部传入 `className` 并拼接到卡片根节点。

## Goals / Non-Goals

**Goals:**
- 桌面端呈现用户指定的固定四行分组（1+2+2+2），窄屏单列按同一顺序堆叠
- 布局顺序只影响【指标总览】图表主区的展示，不改动任何数据产物

**Non-Goals:**
- 不调整 `snapshot.indices` 数据顺序、后端接口或 `pipeline/` 任何计算
- 不改图卡内部内容、时间范围下拉框与信号标记逻辑

## Decisions

### D1：排序放在前端展示层，不改数据端 INDEXES 顺序

- **选择**：在 `signal-hub-web/src/lib/` 新增纯函数与常量（如 `DASHBOARD_INDEX_CHART_ORDER: string[]`，按 code 列出目标顺序：sh000001、sh000688、sz399006、sz399001、sh000300、sh000905、sh000852），提供 `orderIndicesForCharts(indices)` 返回排序后的新数组；未知 code 的指数按原相对顺序排在末尾，保证快照缺项或多项时不崩。
- **备选**：改 `pipeline/fetch_market.py` 的 `INDEXES` 字典顺序。否决原因——数据端口径已固定（AGENTS 规则 5），且 `recent_signals`、`candidates` 等产物字段都受该顺序间接影响，等于换了一套数据历史口径，仅为 UI 布局不值得。
- Dashboard 渲染改为遍历排序后的数组，`grid grid-cols-1 md:grid-cols-2 gap-6` 容器保持不变。

### D2：上证指数独占首行用 `md:col-span-2` 跨列实现

- **选择**：排序后第 1 张卡片（上证指数）传 `className="md:col-span-2"`，其余卡片不跨列。CSS Grid 自动流式布局天然形成 1+2+2+2 四行，无需拆成四个子容器。
- **备选**：按行拆分多个 flex/grid 容器。否决原因——容器数量与断点行为要各写一遍，移动端单列顺序还得单独维护，跨列方案在 `grid-cols-2` 下最简且与现有实现同构。
- 单列（<md）时 `col-span-2` 不生效，卡片按排序数组自然堆叠，满足窄屏场景。

### D3：以 code 而非 name 匹配排序键

- name 可能随数据源文案变化（如"科创50"），code（sh000001 等）是快照内的稳定主键；`tests/` 下按项目既有纯函数测试模式为排序函数补一个单测（顺序正确、未知 code 兜底、缺项不多渲染）。

## Risks / Trade-offs

- [快照中某指数缺失或新增] → 排序函数按 code 匹配、未知项排末尾，页面仍完整渲染现有指数，空状态位置规则不受影响
- [用户后续要求换分组] → 顺序集中在单个常量里，修改只动一处
- [col-span 卡片高度与半宽卡片不同] → 图表高度由卡片内部固定 `h-56`，跨列仅加宽不改高，无挤压风险
