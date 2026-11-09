# Design

## Context

当前对照图（[InstitutionPositionChart.tsx](file:///d:/情绪集合/signal-hub-web/src/components/InstitutionPositionChart.tsx)）在右侧轴用 `benchmark.close` 现场算出沪深300 日涨跌幅百分比（`(close/prev-1)*100`）画一条虚线。数据链路为：

- 管线 [institution_positions.py](file:///d:/情绪集合/pipeline/institution_positions.py) 从 `data/raw/indices-ths/sh000300.csv` 读 `["date","close"]`，产出 `benchmark = {code, name, close}`，并以此为**交易日历**与 `next_ret`/同向率**统计基准**。
- 服务端 [institutions.py](file:///d:/情绪集合/server/institutions.py) `slice_payload` 按 `[lo:hi]` 切 `benchmark.close` 透传。
- 前端类型 [types.ts](file:///d:/情绪集合/signal-hub-web/src/data/types.ts) `InstitutionPositions.benchmark` 只有 `{code, name, close}`。

约束（见 proposal - Why / spec）：右侧要改成**收盘价点位**并同时显示**上证指数 + 沪深300**；而沪深300 仍是唯一的统计与日历基准，**统计口径不得改变**。`sh000001.csv`（上证）与 `sh000300.csv`（沪深300）都含 OHLC，本需求只需 `close`。

## Goals / Non-Goals

**Goals:**
- 图表右侧新增上证指数与沪深300 两条收盘价点位折线，共享同一"指数点位"轴。
- 以最小且不破坏统计口径的方式，把上证指数 close 序列从管线贯通到前端。
- 展示数据与统计数据在数据结构上物理隔离，防止上证被误当作统计基准。

**Non-Goals:**
- 不改 K 线蜡烛图（用户已选收盘价折线）。
- 不改 `benchmark`、`next_ret`、同向率、交易日历等任何既有统计计算。
- 不改历史明细/当日快照中"次日沪深300"相关列与文案。
- 不引入新指数或可配置指数选择器（本轮固定上证 + 沪深300）。

## Decisions

**D1：新增独立的展示序列字段，而非复用/改写 `benchmark`。**
在产物与服务端响应中新增 `market_lines`（前端 `InstitutionPositions.market_lines?`）：`[{code, name, close}, …]`，含上证与沪深300 两条 `{code,name,close}`。`benchmark` 保持原样仅服务统计。
- 理由：物理隔离展示与统计口径（对齐 spec"展示口径与统计基准分离"）；`market_lines` 用数组便于图表按列表渲染、后续可扩。
- 备选：只加一条上证 close 字段、前端把 `benchmark` 当沪深300 线复用——被否，会让图表同时耦合"统计基准"和"展示"两种语义，违背隔离目标。

**D2：上证指数 close 按沪深300 交易日历对齐。**
管线以现有 `dates`（源自沪深300）为准，用 `date→close` 映射回填上证 close，缺失日置 `null`。
- 理由：日期轴对齐是既有契约；两指数交易日偶有出入，缺失需成为断点而非插值。
- 备选：各用各日历——被否，x 轴无法对齐。

**D3：图表右侧用共享"指数点位"轴 + `scale:true`。**
两条指数线同处右轴（数值均在千点量级，`scale:true` 自适应），左轴维持"净持仓（手）"。tooltip：指数后缀"点"、持仓后缀"手"；沪深300 与上证指数各取一种与三家机构色（brand/ocean/slate）不冲突的既有 token 色。
- 备选：每条指数各设一个右侧轴——被否，三轴拥挤、可读性差。

**D4：`market_lines` 设为可选并前端优雅降级。**
旧产物无该字段时，图表仅回退显示现有沪深300（或按缺失处理），MUST NOT 报错或空屏，直到重建产物。

## Risks / Trade-offs

- [产物未重建导致 `market_lines` 缺失] → 字段可选 + 前端降级；本需求走管线重建（见 tasks）。
- [上证/沪深300 交易日不一致产生断点] → D2 按日历对齐、`connectNulls:false`，断点与悬停"数据缺失"一致。
- [有人误把上证当统计基准] → D1 结构隔离 + spec"展示口径与统计基准分离"场景约束；不触碰 `benchmark`/`next_ret`。
- [双指数点位与净持仓手数量级差异大] → 分置左右轴、各自 `scale:true`，不做跨轴换算。
- [OHLC 字段冗余读取] → 仅读 `close`，不引入 high/low/open，减小产物体积。

## Migration Plan

1. 管线新增 `market_lines` 产出（读 `sh000001.csv` close，对齐 `dates`）；`benchmark`/统计不动。
2. 服务端 `slice_payload` 对 `market_lines` 各 `close` 按 `[lo:hi]` 切片透传。
3. 前端 `types.ts` 增可选字段；图表组件去掉涨跌幅计算、改画两条收盘价点位折线；页面标题/副文案改为"净持仓与大盘 走势对照"。
4. 重建 `institution-positions.json` 并刷新页面验证；核对同向率/次日涨跌与改动前一致（口径未变）。
- 回滚：还原四处代码并重建产物即可，统计口径全程未变，回滚无数据风险。
