# Tasks

> 2026-11-02 核实更新：后端与数据层（第 1–5 章）已完成并验证；服务端接口存在 2 个失败测试与覆盖缺口；前端页面为 mock 实现，需整体重做；新增 (经纪) 后缀口径修复（第 9 章）。已完成项的验证证据见各条注记与 `design.md` 构建记录。

## 1. 数据抓取与原始缓存

- [x] 1.1 新增 `pipeline/institution_positions.py`，实现按交易日调用 `ak.get_cffex_rank_table(date, vars_list=["IF","IC","IH","IM"])` 并把四品种明细合并为统一列（`date/variety/contract/rank/party_name/long_oi/short_oi`），验证：对 2024-01-05 调用一次得到 13 个合约、273 行，`variety` 覆盖 IF/IC/IH/IM
- [x] 1.2 实现交易日历取自 `data/raw/indices-ths/sh000300.csv` 的 `date` 列（近 3 年窗口），验证：返回日期集合与指数日线日期集合完全一致，且不含周末
- [x] 1.3 实现原始缓存 `data/raw/cffex/{YYYY-MM-DD}.csv` 的读写与增量补齐：已存在且非空则跳过远端请求，非交易日空返回不写文件，验证：`CacheTest::test_second_call_reuses_cache_and_skips_network`、`test_non_trading_day_is_not_cached` 通过
- [x] 1.4 实现抓取重试与退避（3 次、间隔递增），失败日记录到返回结构而非抛断全流程，验证：`CacheTest::test_retry_then_failure_returns_none`、`PayloadTest::test_failed_day_is_reported_not_raised` 通过

## 2. 会员归一与分组聚合

- [x] 2.1 建立 `MEMBER_ALIASES` 精确匹配映射（至少覆盖 `国泰君安`、`中信期货`），并实现 `guotai`/`citic`/`others` 分组，验证：`MemberGroupingTest` 断言 `中信建投` 落入 `others`、`国泰君安` 落入 `guotai`，且同一会员不重复计入两组
- [x] 2.2 实现按 `(date, variety, member)` 跨合约汇总多单/空单（过滤 `rank<=20`，剔除 `rank=999` 合计行），验证：`AggregationTest::test_member_positions_are_summed_across_contracts` 通过
- [x] 2.3 实现分组守恒校验：三家多单合计必须等于当日该品种 `rank=999` 行的 `long_open_interest`，空单同理，偏差非 0 即抛错，验证：`test_conservation_mismatch_raises` 通过，真实数据全区间 `quality.conservation_checked=true`
- [x] 2.4 实现四品种汇总视图（各品种手数直接相加）与分品种视图并存，验证：`test_all_view_equals_variety_sum` 通过，产物含 `aggregation_note`
- [x] 2.5 输出 `quality.unmatched_members`（未命中映射表但出现在排名中的会员名），验证：`test_unmatched_members_are_reported_without_suffix` 通过

## 3. 净持仓变化与次日关系

- [x] 3.1 实现按品种的"上一交易日"计算 `Δnet`，缺失上一交易日时置 `null` 并标记 `chg_missing`，验证：`SeriesTest::test_change_is_relative_to_previous_trading_day`、`test_missing_day_breaks_change_and_marks_no_data` 通过
- [x] 3.2 实现次日收益率 `next_ret = close_{t+1}/close_t - 1`，其中 `t+1` 取 `sh000300` 日线的下一行而非自然日 +1，验证：`test_next_return_uses_next_trading_row` 通过（跨周末样例）
- [x] 3.3 实现关系判定（`S`/`H`/`P`/`N`）与同向率统计（分母 `S+H`，输出 `decided/same/hedge/pending/start/end`），验证：`test_classify_covers_all_four_states`、`test_stats_use_decided_denominator_and_report_bounds` 通过
- [x] 3.4 校验事后字段隔离：产物中次日相关字段统一以 `next_` 前缀命名，且 `confirmed_index_signals`、`confirmed_sector_trends` 不导入机构模块，验证：构建后 `data/index-signals.json` 与 `data/sector-trends.json` 的 mtime 与内容不变

## 4. 产物契约、校验与原子写入

- [x] 4.1 在 `pipeline/schemas.py` 新增 `institution-positions.json` 的 JSON Schema 与校验入口，覆盖 `as_of/generated_at/window/varieties/institutions/aggregation_note/dates/series/stats/quality` 必填字段与数组等长约束，验证：`PayloadTest::test_payload_matches_schema` 及四个拒绝用例通过
- [x] 4.2 通过 `io_utils.protected_write` 原子写入 `data/institution-positions.json`，校验失败保留旧版并输出原因（`bootstrap.py:update_institutions` 已接入）
- [x] 4.3 新增 `pipeline/test_institution_positions.py` 覆盖归一、聚合、守恒、变化、次日关系、同向率与产物校验，验证：2026-11-02 运行 24 用例全绿
- [x] 4.4 运行一次近 3 年全量构建并记录：728 个交易日（2023-10-30 → 2026-10-30）、`missing_dates=0`、`conservation_checked=true`、`algorithm_version=institution-positions-cffex-top20-v1.0`、产物约 631KB、各机构 × 品种同向率 `decided=684`（统计区间 2023-10-31 → 2026-10-29），详见 `design.md` 构建记录

## 5. 收盘刷新挂载

- [x] 5.1 在 `pipeline/bootstrap.py` 的 `refresh_daily()` 末尾追加机构持仓增量更新，失败仅记录日志不阻断指数与板块刷新，验证：`BootstrapHookTest::test_failure_does_not_break_refresh`、`test_success_reports_as_of` 通过
- [x] 5.2 更新 `README.md`：新增数据源（中金所前 20 会员持仓排名）、产物文件、口径限制（单边手数、前 20 会员、汇总不可跨品种比较）与手动重建命令，验证：README 已含机构多空单口径章节与增量/重建命令

## 6. 服务端接口

- [x] 6.1 在 `server/paths.py` 增加 `INSTITUTION_POSITIONS`，新增 `server/institutions.py` 读取产物并按 mtime 缓存（实现已落地，缓存命中测试缺口并入 6.6）
- [x] 6.2 `GET /api/institutions/positions` 已支持 `institution/variety/from/to/limit`，默认返回近 60 个交易日（`test_default_slice_covers_sixty_days_and_stays_aligned` 通过）；`test_endpoint_returns_slice` 经 6.6 改 TestClient 后通过
- [x] 6.3 实现参数校验：枚举外 `institution`/`variety` 返回 400 并列出允许值，日期格式非法或区间倒置返回 400，合法空区间返回 200 + 空数组，验证：`test_param_validation`、`test_endpoint_rejects_bad_params`、`test_date_range_and_empty_window` 通过
- [x] 6.4 实现缺失与陈旧分支：产物不存在返回 `data_state=preparing`，`as_of` 落后最近交易日时 `stale=true` 并给出 `stale_since`/`stale_reason`，验证：`test_endpoint_preparing_when_payload_missing`（TestClient，202+preparing）、`test_stale_branch_marks_stale` 通过
- [x] 6.5 在 `/api/snapshot` 增加 `institutions` 摘要（仅最新交易日 + 三家净持仓与同向率，不含序列），验证：`test_snapshot_carries_summary_without_series`、`test_summary_has_no_series` 通过
- [x] 6.6 修复两个失败用例：测试直接调用 FastAPI 端点函数时 `from`/`to` 形参拿到 `Query` 默认对象而非 `None`，导致 `validate_params` 抛 `TypeError`；改为经 FastAPI `TestClient` 发真实 HTTP 请求（`.venv` 补装测试依赖 `httpx`），并补齐 mtime 缓存命中（`test_store_caches_by_mtime`）与 stale 分支用例，验证：`python -m pytest server/test_server.py -q` 37 用例全绿
- [x] 6.7 复核 `validate_params` 的健壮性：`date.fromisoformat(value)` 仅捕获 `ValueError`，非字符串输入会抛 `TypeError`；已加 `isinstance(value, str)` 类型守卫，验证：`test_validate_params_rejects_non_string_date` 通过（返回 400 语义而非 500）

## 7. 前端数据层与页面（整体重做，不沿用现有 mock 实现）

> 现状（2026-11-02 核实）：`src/data/institutionClient.ts` 浏览器直连中金所 XML（跨域必失败），失败后回退硬编码 mock 数据；页面含写死的"准确率 64.2%/58.7%""较昨日 +320"等假数值，违反 spec「MUST NOT 以 mock 或占位数值替代真实数据」；手绘 canvas 图表；无品种切换；路由为 `/institution` 而非规划的 `/institutions`。

- [x] 7.1 废弃 `src/data/institutionClient.ts`（删除文件）；在 `src/data/types.ts` 与 `src/data/client.ts` 新增机构持仓响应类型（对齐端点返回的 `as_of/generated_at/stale/institutions/varieties/summary/series/stats/disclosure` 结构）与 `fetchInstitutionPositions(params)`，统一走 `/api/institutions/positions`，验证：`npm run build` 通过且类型无 `any`
- [x] 7.2 新增 `src/hooks/useInstitutionPositions.ts`：进入页面请求一次，品种/机构/时间范围变更时重新请求，复用既有失败保留旧数据语义，MUST NOT 随 30 秒快照轮询重复拉取，验证：断网后页面保留上次数据并显示失败提示
- [x] 7.3 新增 `src/components/InstitutionPositionChart.tsx`：复用 `package.json` 已有 `echarts@^5.6.0`（按需引入 `echarts/core` + 所需图表/组件），双轴折线（净持仓/手 + 沪深300 涨跌幅/%）、断点不连线、图例、十字光标、近 20/60/120 个交易日切换，配色取 `index.css` 变量，验证：`npm run build` 通过，页面渲染 120 日数据无控制台错误
- [x] 7.4 新增 `src/components/InstitutionSummaryTable.tsx`：三家机构当日多单/空单/净持仓/净持仓变化/次日涨跌/关系/同向率，同向率必须带已判定样本数、同向/对冲天数与统计区间，"待判定"显式呈现、小样本（decided<30）显式提示，验证：构造次日缺失数据时该行显示"待判定"而非 0 或空白
- [x] 7.5 重写 `src/pages/InstitutionPositions.tsx`：首屏常驻口径与事后统计声明、品种切换（汇总/IF/IC/IH/IM，默认汇总）、摘要表、折线图、历史明细表（默认 50 行/页 + 机构/品种/日期筛选 + 空状态文案）、数据时点（`as_of`/`generated_at`）与陈旧提示；MUST NOT 出现任何 mock 数据、写死数值或建议式文案，验证：切换品种后表格、图表、统计同步变化；筛选无结果时显示空状态
- [x] 7.6 路由更正：`src/App.tsx` 中 `/institution` 改为 `/institutions`，`src/components/Sidebar.tsx` 导航同步（保留现有 `InstitutionIcon`），验证：点击侧栏进入页面，刷新后路由保持；旧路径不再可达或重定向到新路径

## 8. 集成验收

- [x] 8.1 端到端：启动服务后进入 `/institutions`，验证三家分组顺序（国泰君安 → 中信期货 → 其他机构）、品种切换、图表双序列、明细筛选、同向率样本量与"待判定"均正确呈现，且页面显示 `as_of` 与最近更新时间；全程无 mock 数据
- [x] 8.2 回归：运行 `python -m pytest pipeline server -q`（2026-11-02 结果 217 passed）；本变更相关用例 `pipeline/test_institution_positions.py` + `server/test_server.py` 共 61 用例全绿；`compare_snapshot.py --baseline-sectors __skip_sectors__.json` 现场重建后 **PASS：信号事实与基线完全一致**（7 个指数、73 条已确认信号）。唯一失败 `pipeline/run_self_test.py::test_openspec_validate` 为历史遗留元数据校验（引用已不存在的 `topbottom-signal-external-survey`），与机构多空单、行情数据和信号计算无关；经用户确认按既有问题豁免，不在本次变更中修复
- [x] 8.3 口径自检：抽样 3 个交易日（2026-10-30、2025-06-16、2024-03-15），用原始 `data/raw/cffex/{date}.csv` 重新聚合并与产物逐品种/逐机构比对多单、空单共 45 项全部一致；`2024-03-15` 的 `中信期货(经纪)`/`国泰君安(经纪)` 已分别归入 `citic`/`guotai`；核对结果已记录在 `design.md`「集成验收记录」

## 9. 数据口径修复：(经纪) 后缀会员归一（2026-11-02 用户确认）

> 背景：2024-02-26 至 2024-04-26 共 43 个交易日，中金所源数据会员名带"(经纪)"后缀（如 `中信期货(经纪)`、`国泰君安(经纪)`），精确匹配映射未覆盖，这些交易日的中信/国泰持仓全部落入"其他机构"，分组序列与 `Δnet` 失真。

- [x] 9.1 在 `MEMBER_ALIASES` 追加 `中信期货(经纪)` → 中信期货、`国泰君安(经纪)` → 国泰君安（保持精确匹配原则，不引入子串匹配），验证：单测断言两条新别名归入 `citic`/`guotai`，且 `中信建投(经纪)` 仍落入 `others`（实施注：经 `BOOK_SUFFIX` 正则追加 `经纪` 后缀实现，与既有 `(代客)` 机制一致；`test_broker_suffix_falls_into_correct_groups` 通过，流水线 23 用例全绿）
- [x] 9.2 用本地缓存 `data/raw/cffex/` 重建 `data/institution-positions.json`（**不重新抓取远端**），验证：43 个受影响交易日的中信/国泰序列非空（43/43）、全区间守恒校验通过（2912 项）、`quality.unmatched_members` 不再包含 `中信期货(经纪)`/`国泰君安(经纪)`
- [x] 9.3 按 AGENTS.md 第 9 条留痕：记录重建前后三家机构同向率（含 `decided` 样本量）对比与本次别名新增原因，写入变更说明，验证：`design.md`「口径修复留痕」含前后对比表，快照存于 `stats-before-broker-fix.json` / `stats-after-broker-fix.json`
