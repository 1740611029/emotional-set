# Tasks

> 2026-11-06：首日级别与预热历史两项修正均已完成，v1.2 报告另存，v1.0/v1.1 原件保留；397 项 Python 测试、44 项前端测试、TypeScript/Vite 构建均通过。页面“加载中”确认归因于前端可见性节流（非缺陷）。用户授权临时覆写 `document.visibilityState` 后，接口、2026-11-03 L3 圆点与真实悬停 tooltip（L3/6 只/971.5 亿元）已确认；同日无三角信号，共存验收点自然不适用。本轮拉 OS 前台后 `take_screenshot` 一次成功，两张验收图已真实落盘到 `.screenshots/`。当前 33/33；8.3、8.4 均已完成，生产 `data/etf-volume-events.json` 已发布为 v1.2，仅剩归档。详见 [implementation-notes.md](implementation-notes.md)。

## 1. ETF 日线数据线

- [x] 1.1 新建 `pipeline/fetch_etf.py`：定义硬编码核心池（588000/159915/510300/510050/512100/510500 及名称、跟踪指数）、`ETF_DAILY_RAW` 与 manifest 路径、`ETF_AMOUNT_COLUMNS`，复用 `fetch_market.call_with_retry` / `save_csv`；验证：`python -c "import fetch_etf; print(len(fetch_etf.ETF_UNIVERSE))"` 在 `pipeline` 目录下输出 6。
- [x] 1.2 实现 `fetch_etf_hist_em(symbol, start, end)`：取东财不复权日线、按 `日期/开盘/收盘/最高/最低/成交量/成交额` 重命名、校验七个字段齐全、`amount` 为数值、按日期去重升序；验证：新增 `pipeline/test_fetch_etf.py` 用合成 DataFrame 覆盖字段缺失与数值化两条用例并通过。
- [x] 1.3 实现清单校验 `check_etf_universe(spot)`：用 `ak.fund_etf_spot_em` 或等价清单核对代码存在且名称一致，不一致直接抛错；验证：测试里构造"代码消失""名称不一致"两种输入，断言抛错且不返回替代标的。
- [x] 1.4 实现 `update_etf_daily()` 增量合并：终点取 `last_completed_trading_day()`，起点含本地尾日，逐值比对后仅在有变化时覆写，历史不足标的在 manifest 标记 `insufficient_history`；验证：测试覆盖"盘中不写未完成 bar""尾日成交额被修正后覆写且不产生重复日期行""行数不足被标记"三条。
- [x] 1.5 实现 `fetch_etf_daily()` 首次全量入口与 manifest 落盘（`data/raw/etf-daily-manifest.json` 含 source/universe/boards/rows/start/end/path），并确保单只失败只告警继续；验证：测试注入一只必失败标的，断言其余标的仍写入且函数不抛错。
- [x] 1.6 实跑一次全量抓取并核对产物：`data/raw/etf-daily/` 下 6 个 CSV、每个 500 行以上、`amount` 为元量级（510300 单日峰值应在 400 亿元量级）；验证：`python -m pytest pipeline/test_fetch_etf.py -q` 通过，且人工核对 manifest 的 start/end 与 CSV 首末行一致。

## 2. 事件识别与统计纯函数

- [x] 2.1 新建 `pipeline/etf_volume_events.py`，冻结参数常量（阈值 1e10、基线带 5e9~1e10、簇间隔 2、MA120、250 日高点、底部区 15%、上行区 8%、前瞻期 1/3/5/10/20、单层下限 8、L3 下限 3、状态基准 `sh000300`），全部集中在一处且带中文注释；验证：`python -c "import etf_volume_events as m; print(m.PREREGISTERED)"` 打印全部键值。
- [x] 2.2 实现日级命中与级别判定 `classify_level(hits)`：1 只 → L1、2~5 只 → L2、6 只 → L3；验证：`pipeline/test_etf_volume_events.py` 覆盖 0/1/2/4/5/6 只六种输入。
- [x] 2.3 实现放量簇合并 `merge_clusters(days, gap)`：间隔 ≤ gap 个交易日合并，事件日取簇首日；验证：测试覆盖间隔恰好等于 gap（合并）与 gap+1（拆分）两个边界，并断言事件日等于首个放量日而非成交额最高日。
- [x] 2.4 实现市场状态分类 `market_state(history, day)`：只用 day 及之前的 MA120 与 250 日高点，输出 `下跌底部区`/`趋势上行区`/`中性过渡区` 三者之一；验证：测试断言三分互斥完备（随机 200 条合成序列每一条都恰好命中一类）。
- [x] 2.5 实现前瞻收益与分层统计 `forward_stats(events, index_history, horizons, baselines)` 与 `wilson_interval(k, n)`：输出每层的 n、区间起止、胜率、均值、最大回撤、Wilson 区间，并生成"同状态非放量日"与"50~100 亿普通放量日"两类基线；验证：测试断言 n < 下限时结论标记为"样本不足，暂不能判断"，且缺基线时不得给出"成立"。
- [x] 2.6 加无未来函数回归测试：把某日之后全部 ETF 成交额与指数价格截断后重跑识别与状态分类，断言该日及之前的放量日，以及事件的首日/首日级别/首日状态完全不变，覆盖簇内截断与后续升级；簇结束日、天数、峰值仅为随数据更新的事后描述，不进入首日收益分层；验证：`pytest pipeline/test_etf_volume_events.py -q` 全绿。

## 3. 生产事件产物与 schema

- [x] 3.1 在 `pipeline/schemas.py` 新增 `ETF_VOLUME_EVENTS_SCHEMA`（Draft2020-12）与 `validate_etf_volume_display`：要求 `days` 日期唯一升序、`level` ∈ {L1,L2,L3}、`etf_codes` ⊆ `pool` 代码、`events.start_date` 必须出现在 `days` 中、金额为正；注册进 `SCHEMAS` 与 `validate_payload` 分派；验证：新增测试对"日期重复/level 非法/events 起点不在 days 中"三种坏数据断言抛错。
- [x] 3.2 在 `pipeline/build_dashboard.py` 新增 `build_etf_volume_events()`（调用第 2 组纯函数，读 `etf-daily` CSV + 沪深300 日线，产出 as_of/generated_at/pool/days/events）并加入 `main()` 的 `protected_write` 序列与 `--validate` 覆盖；验证：`python -m pipeline.build_dashboard --output-dir .tmp-drill-etf` 生成 `etf-volume-events.json` 且 `--validate` 通过。
- [x] 3.3 核对新产物内容与实测事实一致：`days` 中 2024-09-30 与 2026-11-03 的 `level` 必须为 `L3`，且 `etf_codes` 为 6 只全部命中；验证：写一段临时校验脚本读取产物断言这两条，跑通后删除脚本。

## 4. 刷新流程接入

- [x] 4.1 在 `pipeline/bootstrap.py` 把现有 `ensure_fresh_data()` 主体改名为 `_ensure_fresh_data_core()`，新 `ensure_fresh_data()` 调 core 后 best-effort 执行 `update_etf_daily()`，结果并入 `refreshed["etf"]`，异常只打印告警；验证：`pytest pipeline/test_trading_calendar.py pipeline/test_refresh_and_rebuild.py -q` 既有断言全部通过（不需要改这些测试）。
- [x] 4.2 确认 `latest_local_dates()` 与 `data_gap()` 未被改动，ETF 不进 stale 判定；验证：新增一条测试断言 `data_gap()["local"]` 的键集合仍为 indices/sectors/institutions。
- [x] 4.3 在 `pipeline/refresh_and_rebuild.py` 的日志汇总里补 ETF 一行（有更新/跳过/失败），保持既有三行输出格式不变；验证：`python -m pipeline.refresh_and_rebuild --force` 输出包含 ETF 状态行，且原有"数据已覆盖最近交易日"判定文字未被改写。

## 5. 研究报告

- [x] 5.1 新建 `pipeline/run_etf_volume_study.py`（argparse 支持 `--start`、`--end`、`--out`，另支持 `--previous`），读 `etf-daily` + 7 个指数日线，调用第 2 组纯函数产出研究报告；保留起点以前历史用于指标预热及簇首日判定，只在所选区间筛事件、基线、名单与时间分段，不使用终点之后的数据。原 `etf-volume-spike.json` 与 v1.1 原件保留，修正输出 `data/research/etf-volume-spike-v1.2.json`；验证：脚本跑通且 JSON 含 `preregistered`、`input_hash`、`questions`、`lists`、`stats`、`events` 六块，入口回归及同输入生产状态核对通过。
- [x] 5.2 实现 `render_markdown()`：章节固定为「结论先行 / 冻结参数 / 名单核实（核心 6 只 vs 宽基全集）/ 事件清单 / 分层统计（含两类基线、样本量、区间、Wilson 区间）/ 局限与风险」；每个被问判断必须落在 `成立`/`不成立`/`样本不足，暂不能判断` 三态之一；验证：生成的 md 中每个 `questions` 条目都有三态标签，且每处胜率同行带 n 与区间。
- [x] 5.3 实现宽基全集口径筛选：从 ETF 清单中按名称/跟踪指数匹配宽基集合，输出区间内破百亿的完整代码-名称-日期清单，用于判定"只有 6 只"是否成立；验证：报告名单章节同时出现两个清单，且"只有 6 只"条目带反例代码与日期。
- [x] 5.4 生成报告并人工复核结论与实测一致（核心 6 只均破百亿；宽基全集为 10 只；L3 仅 2024-09-30 与 2026-11-03 两次）；验证：`data/research/etf-volume-spike.md` 已生成，且 `git status` 显示 `data/index-signals.json`、`data/sector-trends.json`、`data/etf-volume-events.json` 未被修改。

## 6. 服务端 API

- [x] 6.1 在 `server/snapshot.py` 新增事件产物加载（复用 `PayloadStore` 形态或等价 helper：按 mtime 失效、解析失败返回 `None`），并在快照组装处透出 `etf_volume_events`（按日期升序）；验证：`pytest server/test_server.py -q` 现有用例通过，新增用例断言事件产物存在时字段完整。
- [x] 6.2 在 `server/app.py` 的 `/api/snapshot` 与 `/api/indices/{code}/history` 返回同一份 `etf_volume_events`，缺失/损坏时降级为空数组且不返回 5xx；验证：新增用例覆盖"产物缺失 → 200 且空数组""产物损坏 → 其余字段不变""快照与指数历史两处逐项一致"三条。
- [x] 6.3 确认既有字段零变化：同一指数历史响应除新增字段外逐值一致；验证：用例中比对固定快照的其余键值。

## 7. 前端放量标记

- [x] 7.1 在 `signal-hub-web/src/data/types.ts` 增加 `EtfVolumeEvent` / `EtfVolumeLevel` 类型并在快照类型上挂 `etf_volume_events`；验证：`npm run build` 中 `tsc` 通过。
- [x] 7.2 新建 `signal-hub-web/src/lib/etfVolumeMarkers.ts`：纯函数 `buildEtfVolumeMarkerData(events, dates, closes, minVisibleClose)` 产出追加到同一 `markPoint` 数组的标记（circle、按级别取尺寸 6/8/11 与描边 1/2/2、坐标落在最低收盘 × 0.97），并提供 `formatEtfVolumeTooltip(event)` 输出「日期/级别/命中只数/合计成交额（亿元，一位小数）」；验证：新增 `tests/etfVolumeMarkers.test.ts` 覆盖范围过滤、级别尺寸、坐标下移、金额格式四点。
- [x] 7.3 改造 `indexSignalCharts.ts`：`buildIndexSignalChartOption` 增加可选事件参数，信号标记与放量标记合并到同一 `markPoint.data`，tooltip formatter 同日既命中信号又命中事件时两段拼接；验证：在 `tests/indexSignalCharts.test.ts` 增补"同日双命中两段都在""事件为空时输出与变更前完全一致"两条用例。
- [x] 7.4 改造 `IndexSignalChartCard.tsx`：传入事件数据、在图例行追加放量标记说明（含"单日成交额 ≥ 100 亿元"口径），颜色取 `--color-brand-500`；验证：`npm test` 全绿、`npm run build` 通过，且页面在事件缺失时仅显示原有信号标记。

## 8. 集成验证与留痕

- [x] 8.1 备份并逐条比对：把 `data/index-signals.json`、`data/sector-trends.json` 备份到 `.backup/2026-11-05-etf-volume-spike/`，重建后逐条比对；验证：比对脚本或 diff 输出为空。
- [x] 8.2 全量测试与构建：`pytest pipeline server -q`、`cd signal-hub-web; npm test; npm run build`；验证：全部通过且无新增告警。
- [x] 8.3 端到端核对：启动服务后 `/api/snapshot` 含 `etf_volume_events`，指数卡图在 2026-11-03 显示 L3 标记且 tooltip 写明 6 只命中与合计成交额；验证：浏览器实际访问确认，记录截图到 `.screenshots/`。（2026-11-06 已完成：在用户授权下临时覆写测试 tab 的 `document.visibilityState` 后，`/api/snapshot` 200 返 108 条事件；默认近一年视图下 2026-11-03 L3 圆点坐标可见，canvas 上真实 `mousemove` 触发原生 ECharts tooltip，DOM 读出「2026-11-03 · 级别 L3 · 命中 6 只 · 合计成交额 971.5 亿元」与后端 `97149727269.537` 元逐值一致；同日 7 张指数图的 `markerInfo.date=2026-11-03` 数量=0，共存验收点此日不适用。截图已落盘 `etf-volume-dashboard.png`、`etf-volume-tooltip.png`。未修改代码、dist、生产 JSON 或注入伪数据。）
- [x] 8.4 留痕：在变更归档说明中记录冻结参数、输入哈希、改动前后 `index-signals.json` 逐条一致这一事实，以及本轮结论（含样本量与统计区间）；验证：归档文档中存在上述四项，且与研究报告数值一致。（2026-11-06 已完成：四项均记入 implementation-notes.md「8.4 最终留痕」；发布前后 index/sector 逐字节相同；days 108 条不变、events 仅 4 簇 level 重标；发布后生产 events 区间内 17 簇 start_date/level/market_state 与 v1.2 报告逐项一致，结论表数值同源；输入哈希 v1.2 `0b7cac9f...`、发布前 ETF `946eb24f...`、发布后 `d48cf335...`。）
