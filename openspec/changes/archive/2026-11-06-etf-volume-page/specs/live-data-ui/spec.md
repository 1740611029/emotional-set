# Spec Delta

## REMOVED Requirements

### Requirement: 指数图表 ETF 放量标记

**Reason**: 放量指标改为独立的【ETF 放量】页面呈现，不再作为指数信号卡的叠加图层；指数图恢复为只表达买卖信号。
**Migration**: 全池爆量日（`L3`）的标记与阈值口径改由 `etf-volume-view` 的"合计成交额与上证指数双轴折线图"与"L3 爆量日标记"两条需求承载；`L1`/`L2` 事件不再在任何页面标记，数据仍保留在 `data/etf-volume-events.json` 与 `/api/snapshot` 中供研究使用。

### Requirement: 放量标记图例与 tooltip

**Reason**: 随放量标记一并从指数信号卡移出，指数图 tooltip 不再混合两类信息。
**Migration**: 阈值口径与合计成交额的 tooltip 说明迁移到 `etf-volume-view`（金额同样以亿元、一位小数呈现）。

### Requirement: 事件数据缺失时静默降级

**Reason**: 指数信号卡不再接收放量事件数据，"降级不影响信号标记"的前提已不存在；两条链路解耦后不再需要该保护性需求。
**Migration**: 页面级降级行为改由 `etf-volume-view` 的"数据不可用时的降级"与"L3 爆量日标记 · 事件产物缺失"场景约束；`live-data-api` 的"事件产物缺失时降级"需求保持不变。
