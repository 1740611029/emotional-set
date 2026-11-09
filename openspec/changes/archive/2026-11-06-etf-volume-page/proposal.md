# Proposal

## Why

ETF 放量目前只以绿圆点的形式贴在【指标总览】的 7 张指数信号卡上，用户要看"哪几天爆量、爆量时成交额和大盘各自怎么走、6 只 ETF 谁在放量"必须自己在小图之间来回比对，既看不清量级差异，也看不到单只 ETF 的成交额走势。放量指标应当有自己的一页，把成交额与大盘走势放在同一坐标系里对比。

## What Changes

- 新增独立页面【ETF 放量】（路由 `/etf-volume`，侧边导航新增入口），包含两张各独占一行的折线图与一个页面级时间范围下拉：
  - 第一张图：核心 6 只 ETF 的**合计单日成交额**（左轴，亿元）与**上证指数收盘价**（右轴，点位）双轴对照，并在 L3 全池爆量日画醒目标记。
  - 第二张图：6 只 ETF 各自的**单日成交额**折线，合并到同一张图、同一金额轴，图例可点击隐藏单只。
- **BREAKING**（展示层）：从【指标总览】的指数信号卡上移除 ETF 放量标记及其图例与 tooltip 段落，指数图恢复为只显示买卖信号标记。
- 合计成交额与单只成交额只覆盖**6 只 ETF 全部已有日线的区间**（当前为 2020-11-16 起），未齐备的早期历史不画线、不补零、不用部分标的之和冒充合计。
- 新增只读接口返回 ETF 成交额序列、对齐后的上证指数收盘价与 L3 爆量日；ETF 日线原始数据缺失或损坏时接口降级、页面显示空状态。
- 放量事件本身的识别、分级（L1/L2/L3）与统计口径保持不动：仍由 `etf-volume-spike-v1.2` 生成 `data/etf-volume-events.json`，页面只是把 L3 挑出来显示。**不改动任何顶部/底部信号计算逻辑。**

## Capabilities

### New Capabilities

- `etf-volume-view`: 【ETF 放量】页面的展示与交互契约——两张折线图的内容与口径、时间范围下拉、L3 爆量日标记、金额单位、缺数据降级与不插值原则。

### Modified Capabilities

- `live-data-ui`: 移除【指数图表 ETF 放量标记】【放量标记图例与 tooltip】【事件数据缺失时静默降级】三条需求——放量标记不再出现在指数信号卡上，ETF 数据链路故障与指数图渲染彻底解耦。
- `live-data-api`: 新增【ETF 成交额序列接口】需求——规定序列的齐备区间口径、合计算法、上证指数对齐方式与降级行为。既有 `etf_volume_events` 快照字段保持不变。

## Impact

- 前端：新增 `signal-hub-web/src/pages/EtfVolume.tsx`、图表组件与纯函数模块（含 `node:test` 单测）；`App.tsx` 新增路由、`Sidebar.tsx` 新增菜单项；`Dashboard.tsx`/`IndexSignalChartCard.tsx`/`lib/indexSignalCharts.ts` 去掉放量标记入参与分支，`lib/etfVolumeMarkers.ts` 与其测试随之外移或改造；`data/client.ts`、`data/types.ts` 新增 ETF 序列接口与类型。
- 后端：`server/` 新增只读序列模块与端点（读 `data/raw/etf-daily/*.csv`、`data/raw/indices-ths/sh000001.csv`，复用 `etf-volume-events.json` 的 L3 事件），`server/test_server.py` 补测试用例。
- 数据层：不新增落盘产物、不改流水线与重建链路，`pipeline/` 与 `data/*.json` 均不动。
- 风险：合计成交额是展示层求和，严禁回流到任何信号或放量识别输入。
