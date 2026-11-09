# Tasks

## 1. 数据管线：新增展示用指数序列

- [x] 1.1 在 [institution_positions.py](file:///d:/情绪集合/pipeline/institution_positions.py) 新增上证指数日线收盘价读取（`data/raw/indices-ths/sh000001.csv`，仅 `["date","close"]`），并按现有 `dates`（沪深300 交易日历）用 `date→close` 映射对齐，缺失日置 `null`；验证：对齐后数组长度等于 `len(dates)`，非交易日位置为 `null`
- [x] 1.2 在 `build_payload` 返回体新增 `market_lines`：`[{code:"sh000001", name:"上证指数", close:[...]}, {code:"sh000300", name:"沪深300", close: closes}]`；`benchmark`、`next_ret`、`stats`、交易日历计算一字不改；验证：产物含 `market_lines`，且 `benchmark` 与原逻辑保持仅沪深300
- [x] 1.3 扩展 [test_institution_positions.py](file:///d:/情绪集合/pipeline/test_institution_positions.py)：断言 `market_lines` 存在、两条序列与 `dates` 等长、上证缺失日为 `null`，并断言 `benchmark`/`stats`/`next_ret` 与未加该字段前的基线逐值一致；验证：`pytest pipeline/test_institution_positions.py` 通过
- [x] 1.4 重建 `data/institution-positions.json`（跑管线 `main`）；验证：落盘产物含两条 `market_lines`，`protected_write` 校验通过

## 2. 服务端切片透传

- [x] 2.1 在 [institutions.py](file:///d:/情绪集合/server/institutions.py) `slice_payload` 对 `market_lines` 每条 `close` 按同一 `[lo:hi]` 切片透传（旧产物无该字段时返回空列表，不报错）；验证：切片后各 `market_lines.close` 与返回 `dates` 等长
- [x] 2.2 在 [test_server.py](file:///d:/情绪集合/server/test_server.py) 补：给定含 `market_lines` 的 payload，`/api` 响应 `market_lines` 与 `dates` 同窗对齐，且 `benchmark`/`stats`/`summary` 数值与不含该字段时一致；验证：`pytest server/test_server.py` 通过

## 3. 前端契约与图表改造

- [x] 3.1 在 [types.ts](file:///d:/情绪集合/signal-hub-web/src/data/types.ts) `InstitutionPositions` 新增可选字段 `market_lines?: { code: string; name: string; close: (number | null)[] }[]`；验证：`npm run build` 类型检查通过
- [x] 3.2 改造 [InstitutionPositionChart.tsx](file:///d:/情绪集合/signal-hub-web/src/components/InstitutionPositionChart.tsx)：删除 `(close/prev-1)*100` 涨跌幅计算，改为读取 `market_lines`（缺省回退用 `benchmark.close` 作沪深300 单线）在右侧轴画收盘价点位折线；`market_lines` 经 props 传入并随 `[start:]` 切片；验证：图中出现上证 + 沪深300 两条线，日期轴与净持仓对齐
- [x] 3.3 同组件调整坐标轴、图例与 tooltip：右侧 `yAxis` 名称改"指数点位"、去掉 `{value}%` 格式化、`scale:true`；tooltip 对指数序列后缀"点"、净持仓后缀"手"；上证指数取与三家机构色不冲突的独立色；更新文件顶部注释；验证：悬停显示点位、图例可切换、无百分号
- [x] 3.4 在 [InstitutionPositions.tsx](file:///d:/情绪集合/signal-hub-web/src/pages/InstitutionPositions.tsx) 把图卡标题改为"净持仓与大盘 走势对照"、副文案同步为大盘收盘价点位对照语义，并把 `market_lines` 传入图表；验证：页面标题正确、无"沪深300 走势对照"字样
- [x] 3.5 缺失断点校验：构造某日上证或净持仓为 `null` 的数据，确认断点显示且悬停标"数据缺失"，不以插值/零值连接；验证：`npm run build` 与现有 `tests` 通过，浏览器抽查断点

## 4. 集成与口径回归

- [x] 4.1 启动服务并在浏览器打开机构多空单页，确认对照图同时呈现净持仓折线（左轴·手）与上证、沪深300 收盘价折线（右轴·点），标题为"净持仓与大盘 走势对照"
- [x] 4.2 口径回归：对照改动前，确认当日快照、历史明细"次日沪深300"、同向率与同向/对冲判定数值完全未变（上证指数仅为展示叠加）
- [x] 4.3 运行 `openspec validate --all --strict` 全绿（0 failed），确认 delta 场景标题与主 spec 同步无冲突
