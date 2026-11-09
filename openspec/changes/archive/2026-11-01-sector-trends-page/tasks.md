# Tasks

## 1. 基线与算法变更（二级独立判定）

- [x] 1.1 固化改动前基线：用改动前的 `sector-donchian20-ma60-v1.0` 代码对同一份原始日线重建产物，写入 `data/research/sector-trends-baseline.json`，验证：文件可解析、为旧结构（无 `dates` 字段），一级 31 个 + 二级 131 个
- [x] 1.2 修改 `pipeline/confirmed_sector_trends.py` 的 `calculate_sector_universe()`：删除二级板块 `state`/`days` 赋值父级的逻辑，改为使用自身 `calculate_sector_trend()` 结果；保留"父级名称必须存在"校验；滞后板块输出自身 `date` 并标记 `data_lag`，滞后超 30 个自然日时 `state` 置空，验证：`pytest pipeline/test_confirmed_sector_trends.py` 22 passed，新增用例覆盖"二级状态可与父级不同""历史不足不回退父级""滞后超 30 日置空""缺少父级报错"
- [x] 1.3 新增强度构成导出：为每个板块输出 `strength_parts{range_position, ma_score}` 与 `strength_basis`（`directional`/`inverted`/`neutral`），验证：`schemas.validate_sector_display` 对全部 162 个板块逐条校验算式自洽（误差 < 0.001），生成时校验通过
- [x] 1.4 新增 `sector_history(frame, days)`，复用 `calculate_sector_trend()` 输出最近 250 日的 `date`/`close`/`state`，验证：单测覆盖正常历史、历史不足、非法 days，且序列末项等于该板块条目的 `date` 与 `state`
- [x] 1.5 新增二级板块独立状态评估 `build_subsector_evaluation`：按四态 × 10/20 日 × 研究段/验证段输出触发数、已判定样本数、成功数、胜率、Wilson 95% 区间与小样本标记，验证：输出覆盖 4 状态 × 2 观察期 × 2 分段，空样本时 `rate` 为 null 且样本数为 0
- [x] 1.6 在 `pipeline/build_dashboard.py` 的 `build_sectors()` 中组装扩展产物：顶层共享 `dates`、每板块 `date`/`state`/`data_anomaly`/`data_lag`/`strength_parts`/`strength_basis`/`series.close`/`series.state`/`spark`，二级条目补 `code`，并挂载 `subsector_evaluation`，验证：`schemas.validate_payload` 通过，31 个一级 + 131 个二级条目齐全，日期序列只出现一次
- [x] 1.7 扩展 `pipeline/compare_snapshot.py` 的对照口径为两段判定：一级 31 条必须零变化；二级输出状态变化清单，验证：运行后一级差异为空、指数信号事实一致，二级清单 19 条（食品饮料 4、家用电器 6、交通运输 3 等）
- [x] 1.8 重新生成 `data/sector-trends.json`，验证：体积 704KB（标量数组压成一行写入）、一级分组条数与基线完全一致（bull 4 / bear 25 / rise 2）、无晚于 `as_of` 的交易日
- [x] 1.9 在根 `README.md` 增补板块趋势说明：数据来源、四态口径与算法版本、二级独立判定规则、强度公式与取反规则（含可复算示例）、二级评估结论与样本量、重新生成命令，验证：文档中的命令按原样可执行

## 2. 服务端接口扩展

- [x] 2.1 修改 `server/snapshot.py` 的 `_sector_block`：透传摘要字段与 `spark`、二级条目 `code`、`subsector_evaluation`，剔除 `dates`/`series` 长序列，验证：快照 `sectors` 中不含 `dates`，板块块体积 94KB（未瘦身时约 700KB）
- [x] 2.2 在 `server/app.py` 新增 `GET /api/sectors/{code}/history`，支持 `days=60|120|250`（默认 120），非法 `days` 返回 400、未知 `code` 返回 404，验证：三档正常返回且末项日期等于板块 `date`；`days=999` 返回 400、`days=abc` 返回 422；未知代码返回 404
- [x] 2.3 验证二级板块可单独查询，验证：以二级代码请求返回 `level=second`、`parent` 为其一级板块名称，且序列与父级不同
- [x] 2.4 历史端点沿用陈旧降级语义，验证：快照 `stale=true` 时端点返回 `stale=true` 与 `stale_since`，不返回空序列
- [x] 2.5 补充 `server/test_server.py` 用例覆盖新端点、快照瘦身与二级查询，验证：`pytest server` 25 passed（含新增 `SectorSnapshotTest` 与 `SectorHistoryApiTest`）

## 3. 前端类型与数据层

- [x] 3.1 扩展 `src/data/types.ts`：新增 `SectorBaseItem`/`SectorItem`/`SectorSubItem`/`SectorStrengthParts`/`SectorHistory`/`SubsectorEvaluation` 等类型，验证：`npm run build` 中 `tsc` 通过
- [x] 3.2 在 `src/data/client.ts` 新增 `fetchSectorHistory(code, days, signal)`，沿用既有 `ApiError` 与超时处理，验证：非 2xx 时抛出携带服务端 detail 的 `ApiError`
- [x] 3.3 新增 `useSectorHistory(code, days)` hook：切换板块/范围时请求、失败保留上一次序列并给出错误、卸载时 abort，验证：请求在 `useEffect` 清理中 abort，返回 `code` 供页面避免错位展示
- [x] 3.4 为纯函数与数据契约编写测试，验证：`npm test` 7 passed（压缩区间、移动平均、强度算式、分布统计、格式化）

## 4. 走势图组件

- [x] 4.1 安装 `echarts@^5.6.0` 并按需引入 Line/Grid/Tooltip/MarkArea/DataZoom，验证：`npm run build` 通过，echarts 只进入 `Sectors` 懒加载分包（530KB / gzip 178KB），主包仍为 299KB
- [x] 4.2 实现 `SectorTrendChart`：收盘折线 + MA60 均线 + `markArea` 四态色带 + `axisPointer` 悬停读数，验证：构建通过，图表数据由历史接口提供，悬停显示日期/收盘/状态
- [x] 4.3 实现状态序列压缩函数 `compressStates`，验证：单测覆盖全同状态、逐日切换、全空值三种情况
- [x] 4.4 实现时间范围切换 60/120/250，验证：切换后按所选长度重新请求并重绘，不触发整页重载
- [x] 4.5 处理历史不足与状态缺失，验证：`insufficient` 为真时提示按实际长度绘制；状态为空时不生成色带
- [x] 4.6 四态配色从 CSS 变量读取（`--color-mint-500` 等），验证：JS 中只保留兜底色值，主配色来自 `index.css`
- [x] 4.7 处理实例生命周期，验证：`echarts.init` 只创建一次，`ResizeObserver` 与 `dispose` 在清理函数中执行

## 5. 页面与导航

- [x] 5.1 实现 `SectorStateBadge`：四态中文标签、`state` 为空时"数据不足"、二级与父级不一致时"与一级（X）不一致"，验证：文案中无"买入/卖出"
- [x] 5.2 实现左侧板块列表：四态筛选 chip、强度降序、名称/代码检索、命中数量，验证：筛选与检索为内存计算，空结果时显示"无匹配板块"
- [x] 5.3 实现跨状态不可比提示，验证：`filter=all` 且按强度排序时显示提示，单状态筛选时不显示
- [x] 5.4 将筛选、排序、检索与选中板块写入 URL query，验证：`state`/`sort`/`q`/`code` 均可从 URL 还原，切换筛选时清除选中项
- [x] 5.5 实现右侧详情：状态摘要（状态、强度、强度口径、持续天数、数据日）、走势图、内部状态分布、二级细分板块表（含独立状态与差异提示），验证：二级表可点击切换到该子板块自身走势
- [x] 5.6 实现强度解释面板 `StrengthExplainer`：口径说明、取反规则、两项构成分与可复算算式、跨状态不可比与异常固定 50，验证：算式由 `explainStrength` 单测覆盖，页面展示当前板块实际构成值
- [x] 5.7 实现二级板块独立状态评估区块：四态 × 10/20 日胜率、已判定样本数、研究段/验证段分列、稳定性提示，验证：区块可展开，单元格同时给出百分比与样本数
- [x] 5.8 注册 `/sectors` 路由、侧边栏"板块"分组与图标、`TopBar` 标题映射，并对页面做 `React.lazy` 懒加载，验证：构建产物中 echarts 仅在 `Sectors` chunk；`/sectors` 由服务端返回 200
- [x] 5.9 实现加载、失败、陈旧、`data_anomaly`、`data_lag` 与"走势时点与快照不一致"提示，验证：页面包含六类提示分支，失败时保留旧数据
- [x] 5.10 在页面固定展示描述性说明（板块趋势为描述性分类、方向胜率不代表交易收益、不构成买卖建议），验证：文案位于详情区末尾且不随筛选隐藏

## 6. 集成验收

- [x] 6.1 全站回归，验证：`npm run build` 通过；`pytest pipeline server` 中板块与接口相关用例全部通过（3 个失败用例属于并行开发中的 `institutions` / `factor_survey`，与本次变更无关，已在交付说明中标注）
- [x] 6.2 按三份 spec 的场景逐条验收，验证：接口层用 ASGI 直连验证 200/400/404/422 与字段完整性，页面层由构建与单测覆盖；未覆盖项（真实浏览器交互）已在交付说明中标注
- [x] 6.3 记录本次算法变更影响结论：一级 31 条零变化、二级 19 条变化清单、强度构成自洽、二级评估分段胜率与样本量、文件体积 24KB → 704KB，验证：结论写入 `openspec/changes/sector-trends-page/impact.md`，与 1.7 对照输出一致
