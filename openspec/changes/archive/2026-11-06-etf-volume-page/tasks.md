# Tasks

## 1. 后端：ETF 成交额序列模块

- [x] 1.1 在 `server/paths.py` 新增 `ETF_DAILY_RAW`、`ETF_DAILY_MANIFEST`、`INDEX_THS_RAW` 常量与上证指数代码常量，并注明"展示层只读、不参与信号计算"，确认服务启动日志无 akshare 相关导入错误
- [x] 1.2 新建 `server/etf_volume.py`：读 manifest 的 `universe` 与 6 份 ETF 日线 CSV，按 `date` 集合交集生成齐备区间日期轴，产出每只成交额数组（元）、合计成交额数组与覆盖度信息；用临时目录合成 CSV 夹具写 `server/test_server.py` 用例，断言起点为交集首日、任一只缺数的日期整日剔除、合计逐日等于 6 只之和
- [x] 1.3 在 `server/etf_volume.py` 中按日期查表对齐 `sh000001` 收盘价（缺失为 `null`，不插值），补测试断言指数缺日时 ETF 数值不受影响
- [x] 1.4 从 `PayloadStore.etf_volume_events()` 过滤 `level == "L3"` 得到 `l3_dates`，只保留落在齐备区间内的日期；补测试断言产物缺失/损坏时 `l3_dates` 为空且序列字段照常返回
- [x] 1.5 实现按输入文件 max(mtime) 缓存整份结果，并把所有读盘异常降级为 `data_state="preparing"` + 可读原因；补测试断言文件缺失与内容损坏都不抛出 5xx
- [x] 1.6 用真实生产 CSV 抽样核对：端点结果与手工 pandas 求和逐日一致，且单只当日成交额（换算亿元后）与东财/通达信显示对得上，记录核对的日期与数值

## 2. 后端：序列端点

- [x] 2.1 在 `server/app.py` 注册 `GET /api/etf/volume-series`，返回 `data_state/as_of/generated_at/stale/stale_reason/coverage/dates/etfs/total_amount/index_close/l3_dates`，确认 `/api/snapshot` 响应结构与体积不因新端点变化
- [x] 2.2 在 `server/test_server.py` 补端点用例：字段齐全且各数组与 `dates` 等长、重复请求结果逐字节一致、请求前后 `data/` 下产物内容与 mtime 不变（只读保证）
- [x] 2.3 服务启动后手工 `curl` 该端点并检查 JSON 可解析、日期升序、金额单位为元，同时确认 `/api/snapshot` 的 `etf_volume_events` 字段仍原样存在

## 3. 前端：数据层与纯函数

- [x] 3.1 在 `data/types.ts` 新增 `EtfVolumeSeries` 及条目类型，确认 `npm run build` 的类型检查通过
- [x] 3.2 在 `data/client.ts` 新增 `fetchEtfVolumeSeries`（结构校验、202 解析、`ApiError` 归一化、超时与 abort 透传），补 `node:test` 用例覆盖"缺字段报错"与"202 返回准备中状态"
- [x] 3.3 新建 `src/lib/etfVolumeSeries.ts` 纯函数：按 `INDEX_CHART_RANGES` 切片、元转亿元保留一位小数、双轴 option 构造、6 线 option 构造、L3 markPoint datum 构造；确认切片对同一区间重复调用结果完全一致
- [x] 3.4 新建 `tests/etfVolumeSeries.test.ts`，断言档位换算（近三个月=66 个交易日、全部=齐备区间全长）、金额单位与一位小数、L3 只保留 `L3` 日期、`connectNulls` 为 false 且缺失点保持 `null`，并运行 `npm test` 全绿

## 4. 前端：ETF 放量页面

- [x] 4.1 新建 `components/EtfVolumeCharts.tsx`，实现 `TotalVolumeIndexChart`（左轴合计亿元、右轴上证指数点位、L3 markPoint）与 `EtfAmountLinesChart`（6 条线共用亿元轴 + 可点击图例），确认颜色全部取自 `cssVar` 既有变量、无新增十六进制主色
- [x] 4.2 新建 `hooks/useEtfVolumeSeries.ts`（首次进入请求一次、失败保留上次数据、手动刷新重取），确认切换时间范围不会触发新的网络请求
- [x] 4.3 新建 `pages/EtfVolume.tsx`：`IndicatorHeader` + 口径 `Notice`（成交额与爆量日均为事后事实、不构成信号；阈值 100 亿元；序列起点及成因）+ 页面级时间范围下拉（默认近一年）+ 两张各独占一行的图 + 陈旧/准备中/失败三态空状态；浏览器进入页面确认两图同步切档且区间外历史不画线
- [x] 4.4 在 `App.tsx` 用 `lazy()` 注册 `/etf-volume` 路由，在 `Sidebar.tsx` 新增分组「量能 → ETF 放量」（图标复用 `VolumeIcon`）并同步 `titleByPath`，确认菜单点击、直接刷新地址与页面标题三者一致
- [x] 4.5 断网或停服务后打开页面，确认显示不可用状态与重试入口且不以 0 或空数组绘制占位折线；恢复服务后点击手动刷新可正常出图

## 5. 指标总览：移除放量标记

- [x] 5.1 从 `IndexSignalChartCard.tsx` 删除 `etfVolumeEvents` prop 与放量图例段，从 `Dashboard.tsx` 删除 prop 透传与 `NO_ETF_VOLUME_EVENTS` 常量，确认指标总览渲染正常且信号标记位置无变化
- [x] 5.2 从 `lib/indexSignalCharts.ts` 删除 `etfVolumeEvents` 形参、`volumeMarkers` 分支、为标记让位的 `yAxis.min` 逻辑与 `palette.volume`，并删除 `lib/etfVolumeMarkers.ts`、`tests/etfVolumeMarkers.test.ts` 与 `indexSignalCharts.test.ts` 中的放量相关断言，运行 `npm test` 确认剩余信号用例全绿
- [x] 5.3 从 `data/types.ts` 删除前端已无消费者的 `EtfVolumeEvent`/`EtfVolumeLevel` 与 `Snapshot.etf_volume_events`，运行 `npm run build` 确认无未使用类型与编译错误
- [x] 5.4 打开指标总览逐张检查 7 个指数卡：只出现底部/顶部信号标记与候选标记，无放量圆点、无放量图例、tooltip 不再混入放量段落

## 6. 集成核对

- [x] 6.1 发布前备份 `data/index-signals.json`、`data/sector-trends.json`、`data/etf-volume-events.json` 并记录哈希，重建信号数据后逐值核对三份产物哈希不变，确认本变更未触碰任何信号与放量计算
- [x] 6.2 全量回归：`pytest`（后端与流水线）与 `npm test`、`npm run build` 全部通过，且确认测试运行期间生产 `data/` 产物 mtime 未变
- [x] 6.3 端到端人工核对：ETF 放量页近一年/近三年/全部三档下合计线、指数线与 L3 标记互不遮挡且 tooltip 金额点位齐全；指标总览与机构持仓、板块趋势页行为与改动前一致
