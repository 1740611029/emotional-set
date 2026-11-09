# Design

## Context

动机见 proposal.md - Why，需求见 `specs/etf-volume-view/spec.md`、`specs/live-data-api/spec.md`、`specs/live-data-ui/spec.md`。当前状态与约束：

- ETF 日线由 [fetch_etf.py](file:///d:/情绪集合/pipeline/fetch_etf.py) 增量落盘到 `data/raw/etf-daily/{code}.csv`，列为 `date,open,close,high,low,volume,amount`，`amount` 单位为元，口径固定为东财不复权；6 只代码/名称/跟踪指数同时写在 `data/raw/etf-daily-manifest.json` 的 `universe` 里。实测 6 份日线都更新到最近交易日，起点分别为 2005/2011/2012/2013/2016/2020-11-16。
- 放量级别 L1/L2/L3 由 `etf-volume-spike-v1.2` 在构建期算好，落 `data/etf-volume-events.json` 的 `days`（含 `date/level/etf_codes/total_amount/state`）；服务端 `PayloadStore.etf_volume_events()` 读它并在缺失/损坏时降级为空数组。
- [paths.py](file:///d:/情绪集合/server/paths.py) 明确写着服务端启动链路**不 import `fetch_market`/`fetch_etf`**，以免把 akshare 拉进服务进程；`snapshot.py` 已有直接读日线 CSV 的先例（`HistoryStore`、`breadth_frame`），都按 mtime 缓存。
- 指数日线在 `data/raw/indices-ths/sh000001.csv`（上证指数），与 ETF 日线同为 `date` 字符串升序。
- 前端指数图的档位与切片纯函数已存在：`INDEX_CHART_RANGES` + `sliceIndexHistory`（`lib/indexSignalCharts.ts`），放量标记散落在 `lib/etfVolumeMarkers.ts`、`indexSignalCharts.ts` 的 `volumeMarkers`/`palette.volume` 分支、`IndexSignalChartCard.tsx` 的图例段与 `Dashboard.tsx` 的 prop 透传。
- 双轴图的既有范式：`components/InstitutionPositionChart.tsx`（左轴数值、右轴指数点位、`connectNulls: false`、图例可切换、`cssVar` 取色）。

## Goals / Non-Goals

**Goals:**

- 一次请求拿到 ETF 成交额序列 + 对齐的上证指数 + L3 爆量日，页面纯前端切档，不重复请求。
- 序列组装完全在展示层，流水线、落盘产物、信号计算零改动。
- 指标总览的指数图与 ETF 数据链路彻底解耦：ETF 侧任何故障不得影响指数图渲染。

**Non-Goals:**

- 不改放量识别算法、阈值、分级口径，也不新增放量统计展示（分层胜率仍只在研究报告里）。
- 不新增落盘 JSON 产物，不改 `refresh_and_rebuild` / `build_dashboard` / `PayloadStore.version()` 三键就绪判定。
- 不做 L1/L2 的任何展示；不做归一化视图、不做日期区间自定义输入（本期只有档位下拉）。
- 不动机构持仓页与板块页的代码。

## Decisions

### 1. 序列在服务器读盘组装，不新增落盘产物

新增 `server/etf_volume.py`：读 `data/raw/etf-daily/*.csv` + `etf-daily-manifest.json`（取 universe）+ `data/raw/indices-ths/sh000001.csv` + 复用 `PayloadStore.etf_volume_events()` 的 L3 日期，返回列式 dict，按输入文件 max(mtime) 缓存整份结果。

- 备选 A（新增 `data/etf-volume-series.json` 产物）：要动 `build_dashboard.main()` 的 names 列表、`schemas.SCHEMAS` + 专项校验、`paths`、`PayloadStore.version()` 与 scheduler 的就绪/重算判定，等于给刷新链路加第四个键；一旦不一致会拖累信号产物重建，风险与本需求不成比例。
- 备选 B（前端直接抓 CSV 或 6 次请求）：CSV 未静态暴露，且 6 次请求的日期对齐要放到浏览器做，违背"口径由一处定义"。
- 读盘组装与 `HistoryStore`/`breadth_frame` 同源同法，且 ETF 日线本身已由增量线维护到位，展示层不做任何二次采集。

### 2. universe 取自 manifest，不 import `fetch_etf`

服务端只读 manifest 的 `universe`（code/name/index，顺序即展示顺序），避免把 akshare 拉进服务进程，同时与采集端天然同源。manifest 缺失或缺 6 只时按"数据不可用"降级，不做硬编码兜底名单——兜底名单会与采集端漂移。

### 3. 齐备区间 = 6 份日线 `date` 集合的交集

以交集（升序）作为唯一日期轴；合计成交额 = 该日 6 只 `amount` 之和；`as_of` = 交集末日。不在交集内的日期（包括区间内个别标的缺数的日子）整日剔除：合计线不画该日，其余 5 只的单只线也不画该日。共享一条 x 轴时，“整日剔除”是唯一不产生歧义的解释：留 `null` 会让线断裂看起来像停牌，而实际上是口径边界。

- 备选（部分标的求和 + tooltip 标注只数）会让同一根线在不同日期口径不同（2 只之和 vs 6 只之和），跨期比较无意义，已排除。

### 4. 上证指数按日期查表对齐，缺失为 `null`

指数线用自己的序列按 ETF 日期轴做字典查表，命中不到就返回 `null`（`connectNulls: false`）。不反向以指数日期为轴，也不向前填充。

### 5. 端点形态：`GET /api/etf/volume-series`，无参数、一次给全区间

齐备区间约 1450 个交易日，列式 JSON 体积可控，前端按 `INDEX_CHART_RANGES` 客户端切片，切换档位零延迟且不重新请求，符合"交互不改数值"的需求。

- 备选（沿用板块历史的 `days` 参数）会让 7 个档位变成 7 种服务端语义，且每切一档都要重新拉数，与页面"只看同一段历史的不同窗口"的用法不符。

### 6. L3 标记由端点直接给出 `l3_dates`

服务端从放量事件产物过滤 `level == "L3"` 的日期列表返回，页面不再依赖 `/api/snapshot` 的 `etf_volume_events`。这样 ETF 页完全独立于快照轮询，不会因为盘中快照重建而拿到半成品，同时级别口径仍由生产产物唯一决定，页面不自算。标记坐标为 `[date, 该日合计成交额]`，落在真实数值上，不再沿用旧标记"最低收盘 × 0.97"的固定偏移避让逻辑。

### 7. 前端拆分：页面 + 两个图表组件 + 一个纯函数模块

- `pages/EtfVolume.tsx`：页面骨架、时间范围下拉、`IndicatorHeader`/`Notice` 口径声明、空状态与陈旧提示、手动刷新。
- `components/EtfVolumeCharts.tsx`：`TotalVolumeIndexChart`（双轴 + L3 markPoint）与 `EtfAmountLinesChart`（6 线同轴），ECharts 按需引入 `LineChart` + `Grid/Tooltip/Legend/MarkPoint/DataZoom`，容器 `h-80`、`ResizeObserver`、`notMerge: true` 与机构页保持一致。
- `lib/etfVolumeSeries.ts`：纯函数（按档位切片、亿元换算与一位小数格式化、tooltip 文本、双轴与多线 option 构造、L3 datum 构造），可用 `node:test` 直跑，沿用 `tests/*.test.ts` 约定。
- `data/client.ts` 加 `fetchEtfVolumeSeries`（结构校验 + `ApiError` 归一化，202 也解析），`data/types.ts` 加 `EtfVolumeSeries`，`hooks/useEtfVolumeSeries.ts` 仿 `useInstitutionPositions` 只做一次请求 + 失败保留上次数据。
- 路由 `/etf-volume` 用 `lazy()` 注册；侧边栏新增分组「量能」，图标复用现成的 `VolumeIcon`，不新增 SVG、不新增主色。
- 六线配色全部取既有变量：`--color-brand-500`、`--color-ocean-400`、`--color-mint-500`、`--color-coral-500`、`--color-brand-700`、`--color-ocean-600`；合计线 `brand-500`，上证指数线 `coral-500`（与机构页"上证珊瑚橙"一致）。

### 8. 指标总览侧只做减法

删除 `Dashboard.tsx` 的 `etfVolumeEvents` 透传与空数组常量、`IndexSignalChartCard.tsx` 的 prop 与图例段、`indexSignalCharts.ts` 的 `etfVolumeEvents` 形参与 `volumeMarkers`/`yAxis.min` 让位分支、`palette.volume`，并删除 `lib/etfVolumeMarkers.ts` 与 `tests/etfVolumeMarkers.test.ts`；`indexSignalCharts.test.ts` 中断言放量标记的用例一并移除，其余信号标记断言保持原样，用于证明"减法没有动信号渲染"。

前端 `types.ts` 删除 `EtfVolumeEvent`/`EtfVolumeLevel` 与 `Snapshot.etf_volume_events`（前端不再消费）；**服务端 `/api/snapshot` 与 `/api/indices/{code}/history` 的 `etf_volume_events` 字段原样保留**（`live-data-api` 既有需求不动，研究报告与对照仍需该产物），属于"接口有、前端不读"的已知冗余，不视为本次要清理的债。

### 9. 验证口径

序列纯函数与端点都用可控的合成 CSV 夹具（临时目录 + patch `paths`），严禁测试写生产 `data/`。端点返回值需与手工用 pandas 对 `data/raw/etf-daily/*.csv` 求和抽样的若干日期逐项一致，并与通达信/东财页面上的单只当日成交额抽查核对单位（亿元）无误。

## Risks / Trade-offs

- [齐备区间把早期历史挡在 2020-11 起，用户可能误以为数据缺失] → 页面在图区旁明示起点及其成因（上市最晚的标的），下拉"全部"档也只显示该起点之后。
- [某只 ETF 单日被采集漏掉会让整日从合计线消失，形成小缺口] → 缺口只由交集规则产生、不做插值，并在 `as_of`/覆盖度字段中暴露区间起止与交易日数，便于发现异常放大。
- [移除指标总览标记后，用户看放量必须换页] → 新页面成为唯一入口且导航同级并列，操作步骤由"看图找点"变为"进页对照双轴"，不再需要跨 7 张小图比对。
- [服务端读盘组装若文件损坏可能 500] → 模块级 try 包裹，异常一律降级为 `data_state=preparing` + 原因，绝不把展示数据链路变成快照故障；沿用 `PayloadStore` 的降级写法。
- [一次返回全区间的响应体积随历史增长] → 区间被齐备约束（约 1450 日）且列式压缩，同时 `live-data-api` 新增"长序列不进快照"场景守住 `/api/snapshot` 体积不变。
- [合计成交额被误用为信号输入] → 设计上是单向只读：`server/etf_volume.py` 不 import 任何信号模块，也不写任何文件；`pipeline/` 不引用该模块，依赖方向单向，杜绝回流。

## Migration Plan

1. 后端先上线只读端点（无前端依赖），用 `curl`/测试确认序列与 L3 日期正确，旧页面行为不变。
2. 前端加页面与路由，验证两张图、档位、图例与降级文案。
3. 最后做指标总览的减法并同步 `live-data-ui` 主 spec（归档时 delta 生效）。
4. 回滚：减法是可逆的独立提交，回滚只需恢复标记渲染分支与 `lib/etfVolumeMarkers.ts`；后端端点与页面为纯新增，可单独摘除，不影响任何数据产物。

## Open Questions

- 无。区间起点解释、金额单位、档位集合与 L1/L2 不展示均已由用户确认并写入规格。
