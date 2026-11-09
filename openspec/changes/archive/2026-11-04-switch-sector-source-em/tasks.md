# Tasks

## 1. 备份与前置

- [x] 1.1 将受影响文件与产物复制到 `.backup/2026-11-03-板块换源/`：`pipeline/fetch_market.py`、`pipeline/bootstrap.py`、`pipeline/build_dashboard.py`、`pipeline/confirmed_sector_trends.py`、`server/app.py`、`server/test_server.py`、`data/sector-trends.json`、`data/raw/sectors-manifest.json`；复制后核对清单齐全（项目无 git，此目录即回滚依据）
- [x] 1.2 与用户确认前置决定（已确认，2026-11-03）：① 单层清单 = **31 个东财行业板块**，以**硬编码 BK 代码 + 每日名称校验**固定（实测推翻初稿「按 `Ⅱ`/`Ⅲ` 后缀过滤」假设：496 个里仅 82 个带后缀，接口无层级字段）；② **breadth（横截面宽度）一起换东财**，用户接受指数顶底信号历史整套重算；③ 既有 change `center-sector-detail-dialog` **先放着不动**（不归档、不废止、不修改）

## 2. 东财板块采集（fetch_market / bootstrap）

- [x] 2.1 在 `pipeline/fetch_market.py` 新增东财清单常量与日线函数：`EM_SECTOR_UNIVERSE` 硬编码 31 组 `(BK 代码, 名称)`，`check_sector_universe()`（实现中的实际函数名，工件对齐 L-3）调 `ak.stock_board_industry_name_em` 逐个校验代码存在且名称一致（不一致则报错，不静默重新匹配），`fetch_sector_hist_em(code, start_date, end_date)` 调 `ak.stock_board_industry_hist_em`（不复权）并统一列名为 `date/open/close/high/low/volume/amount`；重写 `fetch_sectors()` 按 `data/raw/sectors-em-industry/{BKxxxx}.csv` 单层落盘并生成含 `universe`/`universe_changes` 的 `sectors-em-industry-manifest.json`；首次全量落盘后核对板块数=31、首末日期与历史不足标记
- [x] 2.2 新增单测 `pipeline/test_fetch_market.py`：universe 硬编码条目数与名称唯一性、名称校验失败（代码缺失/改名）报错不降级、增量日期段计算（起点含尾日、盘中终点=上一交易日，H-1 后含尾日语义）、全量终点来自交易日历、manifest 留痕（新增/缺失/改名三类）各至少 1 个用例，`pytest pipeline/test_fetch_market.py` 通过
- [x] 2.3 重写 `pipeline/bootstrap.py` 的 `update_sectors()`：读 `sectors-em-industry-manifest.json`，路径改为单层 `SECTOR_RAW / f"{code}.csv"`（同时修正 `latest_local_dates()` 的 `SECTOR_RAW.glob("*/*.csv")` 为单层 glob、复核 `_tail_date` 列位），逐板块按本地尾日增量（起点含尾日；无缓存全量 `start_date="20000101"`），沿用 `call_with_retry`、串行 sleep、60 交易日停更阈值与 `insufficient_history` 标记；把 `ensure_raw_data()` 里「全量拉取申万板块日线」的提示文案改为东财；用 `pytest pipeline/test_refresh_and_rebuild.py` 与手工运行 `python -m bootstrap` 验证增量合并与"已覆盖不请求"
- [x] 2.4 收盘定稿验证：在 15:00 前后各跑一次增量（或以 mock 时间单测），确认 15:00 前终点为上一交易日、当日盘中 bar 不落盘，并核对落盘 CSV 尾日与 `last_completed_trading_day()` 一致

## 3. 产物构建单层化（confirmed_sector_trends / build_dashboard）

- [x] 3.1 `pipeline/confirmed_sector_trends.py`：移除二级父级校验与 `build_subsector_evaluation`，`ALGORITHM_VERSION` 升级为 `sector-donchian20-ma60-v2.0-em-source`，四态与强度计算函数保持逐行不动；更新 `pipeline/test_confirmed_sector_trends.py`（删二级用例、版本号断言更新），pytest 通过
  - 已完成（2026-11-03）：`SectorUniverseTest` 重写为单层 3 个用例（各自独立判定且记录中无 `level/parent/subsectors`、历史不足置未分类、滞后标记与超 30 日清空状态），删除 `SubsectorEvaluationTest` 与孤儿函数 `_build_horizons`；`pytest pipeline/test_confirmed_sector_trends.py` 通过
- [x] 3.2 `pipeline/build_dashboard.py`：`build_sectors()` 读取 `sectors-em-industry-manifest.json`、移除 31 个一级硬校验与 `level/parent/subsectors` 字段及 `subsector_evaluation` 输出，新增 `data_source: "东财行业板块"`，groups 单层组装；核对 `pipeline/test_confirmed_signals.py` 相关断言并更新，pytest 通过
  - 已完成（2026-11-03）：`build_sectors()` 已单层化（清单数与 `EM_SECTOR_UNIVERSE` 不符直接报错），`schemas.py` 的 `SECTOR_ITEM` 以 `additionalProperties: false` 拒收旧层级字段、`SECTOR_SCHEMA` 必填 `data_source`；核对 `pipeline/test_confirmed_signals.py` 后确认该文件只测指数确认信号、无板块断言，无需改动；`pipeline/test_io_utils.py` 的 `SectorContractTest` 按单层重写（旧层级字段拒收、code 重复改为跨组重复、删二级差异用例、新增数据源与清单一致性断言）
- [x] 3.3 全量重建验证：以新源缓存运行重建，检查 `data/sector-trends.json` 板块集合与 manifest 一致、四态取值合法、`strength` 在 ±50 内、`algorithm_version`/`data_source`/`as_of` 齐备（对应产物契约场景）
  - 已完成（2026-11-03）：`algorithm_version=sector-donchian20-ma60-v2.0-em-source`、`data_source=东财行业板块`、`as_of=2026-11-03`；31 个板块（bull 3 / bear 28 / warn 0 / rise 0 / unclassified 0），共享日期 250 天（2025-10-23～2026-11-03），板块代码集合与 `EM_SECTOR_UNIVERSE` 一致，无 `subsector_evaluation` 与二级字段残留，`protected_write` 契约校验通过（仅研究段之前的 OHLC 异常告警，不影响计算）
- [x] 3.4（用户批准的追加范围，选项 1）消除全量测试对生产信号的覆写：`pipeline/build_dashboard.py` 的 `main()` 与命令行新增可选 `--output-dir`，`pipeline/run_self_test.py::test_production_signals_unchanged_without_external_data` 改为将两次重建（保留/移除外部数据）都写入临时目录并比对产物哈希，并断言生产 `data/index-signals.json` 哈希前后不变
  - 背景：原用例直接执行 `build_dashboard.py`，输出路径写死为生产目录，每次 `pytest pipeline` 都会绕过 5.4 的人工核对门禁提前覆盖生产指数信号
  - 验证（2026-11-04）：`pytest pipeline/run_self_test.py` → 5.2 PASSED，跑完全量后两个生产文件 mtime 未变；`pytest pipeline` → 236 passed / 1 failed（仅剩既有的 `test_openspec_validate` 失效）
  - 同时发现：2026-11-03 19:21 一个仍持有改前代码的常驻服务进程按 `server/scheduler.py` 路径把两个生产产物写回了旧算法（板块产物含 `subsector_evaluation`、`v1.1` 版本号，且因该路径不带 `compact_arrays` 而体积 2.1MB），已重新只重建板块产物恢复新口径（不写 `index-signals.json`，遵守 5.4 门禁）

## 4. 新旧口径对照报告（独立，不进生产链路）

- [x] 4.1 新增 `pipeline/sector_source_migration.py`：对旧申万缓存与新东财日线的共有日期区间逐板块输出四态一致率、翻转类型分布、有向强度差与可比样本量（板块数、行业日数、统计区间），报告写 `data/research/sector-source-migration-sw-vs-em.{md,json}`；运行后核对报告含样本量字段
  - 已完成（2026-11-04）：31/31 行业按名称配对成功，无跳过、无历史不足板块；分段与生产 evaluation 划分一致（研究段 2020-10-30～2024-12-31 / 验证段 2025-01-02～2026-11-03）另加展示窗口（最近 250 个可比交易日）；每段均给出可比板块数、行业日样本数与区间起止
- [x] 4.2 对照不影响生产验证：删除报告文件后重跑重建，`data/sector-trends.json` 与删除前逐条一致（对应"对照不进入生产链路"场景）
  - 已完成（2026-11-04）：报告临时移走 → `build_dashboard.py --output-dir 临时目录` 重建 → 重建产物哈希与生产文件逐字节一致（`f6239a49f6feecaf…`），且生产文件未被改动，报告已恢复
- [x] 4.3 将对照报告要点（一致率、翻转分布、历史不足板块清单）提交用户核对，确认后再进入 server/前端切换
  - 已核对通过（2026-11-04）：用户选定「核对通过，继续切 server/前端」；要点为 31/31 同名配对、无跳过、无历史不足板块，研究段一致率 70.59%（30,140 行业日）、验证段 75.46%（13,702 行业日）、bull↔bear 翻转 5.20%/4.25%

## 5. 指数横截面宽度换源与信号重验（breadth）

- [x] 5.1 `pipeline/build_dashboard.py` 的 `load_breadth()` 改读 `data/raw/sectors-em/*.csv`（单层），文件数不足 20 个仍报错不降级，注释与报错文案里的「申万一级」改为东财行业板块；`pipeline/swing_indicators.breadth_table()` 算法本体逐行不动；`pipeline/factor_survey.py` 的 `load_breadth` 同步指向新目录，保证调研口径与生产一致
  - 已完成（2026-11-03）：生产目录实为 `data/raw/sectors-em-industry/`（避开与调研缓存 `sectors-em/` 重复计数），`load_breadth()` 不降级且不足 20 个报错，`breadth_table()` 本体未改，`factor_survey.py` 已同步新目录
- [x] 5.2 同源校验：确认同一批重建中 `index-signals.json` 与 `sector-trends.json` 的行业集合一致，数据时点按「板块 `as_of` 允许领先指数 `as_of` ≤ 1 个交易日且各自显式标注」校验（对应 delta 场景"宽度与板块同源"、"同批重建"、"板块时点领先指数时点"），并确认删除旧 `data/raw/sectors/` 后重建结果逐条不变（"旧源目录不参与生产计算"）
  - 已完成（2026-11-04）：同一批重建下两者行业输入均来自 `data/raw/sectors-em-industry/` 的 31 个板块（`load_breadth` 与 `build_sectors` 读同一目录），当时数据时点均为 as_of 2026-11-03；将 `data/raw/sectors/` 与 `sectors-manifest.json` 临时移走后向临时目录重建，`sector-trends.json` 与 `index-signals.json` 哈希均与存在时逐字节一致，旧目录已原样恢复
  - 审查修正（M-2，2026-11-04）：原记录与原 delta 场景写的「数据时点一致」只在 11-03 那一批成立 —— 15:01 服务收盘刷新后 `sector-trends.json` as_of=2026-11-04、`index-signals.json` as_of=2026-11-03（指数 `as_of` 取七个指数尾日的 min、板块 `as_of` 取 31 个板块尾日的 max，东财板块收盘后即发布而同花顺指数晚间才补齐）。按用户批准的「改 spec 措辞」，`index-signal-engine` delta 已改为：允许板块 `as_of` 领先指数 `as_of` ≤ 1 个交易日，超过则视为发布节奏异常需核查；两份产物与界面 MUST 各自显式标注 `as_of`（板块页已有「数据截至」与滞后提示，指数时点由顶栏 `snapshot.as_of` 展示）
- [x] 5.3 新增独立对照脚本输出宽度与指数信号的新旧对照（写 `data/research/`）：站上 MA20/MA60 占比差的均值与分布（带行业日样本数与区间）、七指数顶底信号的旧/新条数、日期一致数、新增与消失条数（带统计区间起止）；对照产物不进生产链路，删除后重建结果逐条一致
  - 已完成（2026-11-04）：新增 `pipeline/breadth_source_migration.py` → `data/research/breadth-source-migration-sw-vs-em.{md,json}`；宽度差：breadth20 全部区间有效样本 2928 行业日（2014-03-20~2026-11-03）有向均值 +0.0270、绝对差均值 0.0907、P90 0.2258，最近 250 日样本绝对差均值 0.0961；breadth60 有效样本 2679、绝对差均值 0.1021；信号：旧 73 → 新 81（日期一致 45、新增 36、消失 28，其中日期一致且类型也一致 45）；脚本仅在内存重建不写生产，并输出"判定规则未变"证据（两遍构建与生产基线的算法版本/分段边界完全一致）
- [x] 5.4 准确率重验：沿用既有研究段/验证段划分，两段分别给出换源后准确率与样本量（已判定/成功/失败/未决、区间），MUST NOT 调任何阈值或权重；将 5.3/5.4 结果提交用户核对，确认后才允许覆盖生产 `data/index-signals.json`
  - 重验结果（跳 7 指数合并，未决不进入分母）：研究段 bottom 24/24→28/28 均 100%；研究段 top 21/22（95.45%）→28/28（100%）；验证段 bottom 10/13（76.92%）→11/13（84.62%）；验证段 top 8/8→8/8 均 100%。未调任何阈值与权重，报告已给出「算法版本与分段边界两遍构建一致、且与生产基线一致」的可核查证据；样本量仅 8–28，已向用户说明不构成「新源更准」结论
  - 用户已批准覆盖（2026-11-04）：旧宽度版本留痕为 `.backup/2026-11-03-板块换源/index-signals-换源前-旧breadth.json`；重建后 `index-signals.json`（as_of 2026-11-03、81 条）与 `sector-trends.json`（as_of 2026-11-03、31 个东财行业板块）同批同源

## 6. 服务端接口扁平化（server）

- [x] 6.1 `server/app.py`：`_find_sector` 改为按 code 在扁平 groups 内查找，板块条目与历史响应移除 `level/parent`，快照评估不再输出 `subsector_evaluation`；`server/test_server.py` fixture 改为单层结构、断言同步重写，`pytest server/test_server.py` 通过
  - 已完成（2026-11-04）：`app.py` 的 `_find_sector()` 改为单返回值扁平定位（不再遍历 `subsectors`），历史响应去掉 `level`/`parent`，准备中占位去掉 `subsector_evaluation`；`snapshot.py` 的 `_SECTOR_SUMMARY_KEYS` 去掉 `level`/`parent`、`_sector_item()` 去掉 `with_subsectors` 递归、`_sector_block()` 两处去掉 `subsector_evaluation`；`test_server.py` 的 `_sector_payload()` 本就直读生产单层产物无需改数据，二级断言换为 `test_snapshot_sector_entries_are_flat`（逐条断言无 `level/parent/subsectors` 且块内无 `subsector_evaluation`）、`test_any_sector_is_located_by_code_alone`（凭 code 定位、拿到板块自身序列）、`test_all_sectors_are_queryable`（31 个板块逐个拉到历史），滞后用例的 payload 去掉 `subsector_evaluation`
  - 板块相关 13 个用例全部通过；整文件为 54 passed / 3 failed，失败的 `StartupBackfillTest` 三个用例与本变更无关（`server/scheduler.py::_bootstrap()` 已改为启动时不做增量补齐，用例仍断言 `ensure_fresh_data` 被调用），已向用户报告并获批准「修测试用例」，见 6.4
- [x] 6.2 服务启动链路验证：`python -m server` 启动后 `/api/snapshot` 板块条目无 `level/parent/subsectors`、历史端点按 code 可查任一板块且 404/400 行为不变（对应 live-data-api delta 场景）
  - 已完成（2026-11-04 09:25）：用 `python -m server`（PORT=8765）真实启动后校验，`/api/snapshot` 为 ready、板块 `as_of=2026-11-03`、`lag_days=0`、`algorithm_version=sector-donchian20-ma60-v2.0-em-source`，块字段无 `subsector_evaluation`，31 个板块条目（bull 3 / bear 28）逐个检查均无 `level/parent/subsectors`；`/api/sectors/BK0438/history?days=250` 返回 250 点且末项 `2026-11-03`（食品饮料 bull），`/api/sectors/BK1283/history` 默认 120 点且收盘序列与前一板块不同；`BK9999` → 404「未知板块代码」、`days=30` → 400「days 只支持 60 / 120 / 250」；验证后已关闭服务，两个生产产物 mtime 未变
- [x] 6.3（用户批准的追加范围，2026-11-04「改读东财目录」）盘中横截面宽度换源：`server/snapshot.py::breadth_frame()` 由写死的旧申万目录 `data/raw/sectors/first` 改读 `server/paths.py` 新增的 `SECTOR_EM_RAW`（= `data/raw/sectors-em-industry/`，与收盘重建 `fetch_market.EM_SECTOR_RAW` 同一份日线），保留「板块文件不足 20 个返回 None」的盘中降级语义与 mtime 缓存；新增 `BreadthSourceTest` 三个用例（含 `paths.SECTOR_EM_RAW == fetch_market.EM_SECTOR_RAW` 的防路径漂移断言，故盘中与收盘同源成为可执行约束）
  - 背景：板块换源后盘中 provisional 信号与三层确认的量能层仍吃旧申万宽度，而旧目录已停更（尾日 2026-11-03），构成盘中/收盘双口径，影响指数信号
  - 验证（2026-11-04）：临时脚本比对两条链路的宽度表，`breadth_frame()`（盘中路径）与 `build_dashboard.load_breadth()`（收盘路径）输出均为 6500×4、覆盖 2000-01-04～2026-11-03 共 6500 个共有交易日，`pandas.testing.assert_frame_equal` 逐点一致；脚本已删除
  - `paths.py` 不直接 import `fetch_market` 是为了不把 akshare 拉进服务启动链路，该常量用途与不 import 的理由已写入注释
- [x] 6.4（用户批准的追加范围，2026-11-04「修测试用例」）修复既有失效用例：`server/test_server.py` 的 `StartupBackfillTest` 更名为 `StartupBuildTest` 并按 `scheduler` 现状重写为 4 个用例（启动顺序 raw→ensure→snapshot 且不触发增量补齐、原始数据失败仍构建首屏、首屏失败不被吞掉、`_backfill_blocking` 失败保留旧产物并返回 False），`EveningBackfillTest` 与 `scheduler.py` 三处注释里的「申万」改为「东财行业板块」
  - 只动测试与注释，未改任何 scheduler 逻辑；「补齐失败」语义迁移到 `_backfill_blocking` 用例，覆盖没有减少
  - 失效根因经哈希比对确认为本变更之前既有：`server/app.py`、`server/test_server.py` 与 `.backup/2026-11-03-板块换源/` 副本完全一致
  - `pytest server/test_server.py` → 61 passed / 42 subtests passed（6.1 之前为 4 failed + 6 errors）
- [x] 6.5（用户批准的追加范围，2026-11-04「本 change 内修」）统一服务落盘格式：`server/scheduler.py` 三处板块产物写入（`_backfill_blocking`、`_ensure_close_data`、`_tick_close`）补上 `compact_arrays=True`，与 `build_dashboard.main()` 的写法对齐（板块产物含 31 × 250 序列，标量数组压成一行）；指数产物不动，依旧保持缩进格式
  - 根因与影响面：`io_utils.protected_write` 的 `compact_arrays` **只切换 JSON 缩进/压行格式，不改变任何数值**，因此这是纯体积问题（同一数据服务自写为 pretty 格式，体积约为压行版的 15 倍：2.1MB vs 139KB），不影响信号结果
  - 防回退：新增 `ProductWriteFormatTest`（三个写盘路径逐个断言 `protected_write` 的 kwargs：`index-signals.json` 无 `compact_arrays`、`sector-trends.json` 为 `compact_arrays=True`）；`pytest server/test_server.py` → 64 passed / 42 subtests passed

## 7. 前端单层改造（signal-hub-web）

- [x] 7.1 `src/data/types.ts` 扁平化 `SectorItem`（移除 `level/parent/subsectors`）与快照类型（移除 `subsector_evaluation`），`npm run build` 通过且无 TS 报错
  - 已完成（2026-11-04）：`SectorBaseItem`/`SectorSubItem`/`SectorItem` 三层定义合并为扁平 `SectorItem`（无 `level/parent/subsectors`）；`SectorBlock` 去掉 `subsector_evaluation`，随之无引用的 `SubsectorEvaluation`/`SubsectorEvaluationSegment`/`SectorStateEvaluation`/`SectorHorizonEvaluation` 随之删除；`SectorHistory` 去掉 `level/parent`；`npm run build`（tsc + vite）通过（`noUnusedLocals` 开启，已同步清理失效导入）
- [x] 7.2 `src/pages/Sectors.tsx`：移除二级列表、`SubsectorDialog` 与父级差异提示，改为单层四态分组列表 + 单层详情，默认选中强度最高板块；样式沿用现有淡色系统，`npm run build` 通过
  - 已完成（2026-11-04）：删除二级表格卡片、`SubsectorDialog` 与 `SubsectorEvaluationPanel`（该面板只读 `subsector_evaluation`，板块级 `evaluation` 原本就未在页面渲染，因此不新增展示），以及 `isFirstLevel`/`activeFirst`/`subsector`/`subRange`/`subHistory`/`subReady`/`distribution` 等二级专用状态；`firstLevel` 重命名为 `sectors`；默认选中仍为强度最高（无 `code` 时取 `filtered[0]`，而 `filtered` 默认按有符号强度降序）；`SectorStateBadge` 去掉 `parentState` 参数与「与一级不一致」差异提示；页面文案中的「申万一级/二级」改为「东财行业板块」（31 个行业）
- [x] 7.3 `src/lib/sectorNavigation.ts` 移除 `sub` 参数与 `findSubsectorUnderFirst`，同步更新 `tests/sectorNavigation.test.ts`；`src/lib/sectorStates.ts` 与 `tests/sectorStates.test.ts` 按单层数据结构调整，`npm test` 全绿
  - 已完成（2026-11-04）：`SectorNavKey` 去掉 `'sub'`，`nextSectorParams` 简化为「`code` 自身可清、其余键切换时清空 `code`」，删除 `findSubsectorUnderFirst`；sectorNavigation 测试重写为 4 个单层用例（保留筛选/检索、取消选中、切换即清选中、不就地修改入参）；`sectorStates.ts` 删除仅服务于二级分布展示的 `stateDistribution`（无其他调用方），`sortSectorItems` 注释改为「板块排序」并给 fixture 补上 BK 代码、加一条「不合并/不丢弃板块」断言；`npm test` 为 31 passed / 0 failed
- [x] 7.4 浏览器验证：启动站点进入板块趋势页，确认 31 个板块全集展示、默认选中、切换选中无残留、四态筛选/强度排序/检索、60/120/250 切换、悬停读数、数据滞后与降级提示正常，无任何二级入口残留；指标总览页确认指数图表与信号正常渲染
  - 已验证（2026-11-04 11:00，`python -m server` PORT=8765 + Browser 子代理，文本/DOM/接口层全部通过）：副标题为「东财行业板块 31 个行业，各自独立判定四态」、`命中 31 个板块`；对页面 innerText、outerHTML 与 `signal-hub-web/dist` 产物全文检索 `二级/子板块/subsector/一级/申万` 均 0 命中；默认选中强度最高的 `银行 BK1283`（bull 16.9）并回填 `?code=BK1283`；点击 6 个板块（3 bull / 3 bear）切换均无残留、高亮恒为 1 个、各板块历史序列互不相同，非法 `?code=BK9999` 与裸 `/sectors` 均回落默认且不报错；四态筛选计数 全部 31 / 多头 3 / 反转 0 / 退潮 0 / 空头 28 / 未分类 0，与接口 `states` 一致且 3+28=31；排序在 `强度↓`↔`名称` 间切换有效；检索 `食品`→食品饮料、`BK1201`→电子、`半导体`→0 命中（佐证二级/概念板块名已彻底移除）；60/120/250 日切换的覆盖文案与 `/history?days=N` 返回长度逐一对应、无空图与加载失败；`数据截至 2026-11-03`、`lag_days=0`，无滞后/降级/异常提示；控制台 0 条 error、`/api/snapshot` 与 6 个历史请求全 200；指标总览页 7 个指数卡片（含最新价与涨跌）、`已判定胜率 77 样本 97.4%`、最新信号表 8 行、待确认候选 1 行均正常，时间范围切换与侧栏跳转板块页均无报错
  - 像素层（截图与悬停 tooltip）：受 IDE 内置 Browser 视口 0×0 且 `requestAnimationFrame` 停摆阻塞，子代理无法绘制 canvas（非实现缺陷，接口已把 history/signals 正确下发给图表组件）；已向用户报告，用户选定「自己通过预览面板查看后勾掉 7.4」，据此勾选本项
  - 另发现既有小问题：URL 带非法 `code` 时详情回落默认板块但地址栏不被纠正 → 用户批准本 change 内修复，见 7.5
- [x] 7.5（用户批准的追加范围，2026-11-04「本 change 内顺手修」）修正非法 code 的 URL 与内容不一致：`src/pages/Sectors.tsx` 的默认选中写回 effect 条件由 `!selectedCode` 改为「`active` 存在且 `active.code !== selectedCode`」（依赖改为 `[active, selectedCode, setParams]`），使手改/失效链接里的非法 code 在回落时以 replace 方式写回地址栏，其余查询参数不变
  - 浏览器实测（2026-11-04 11:20，PORT=8765）7 项全通过：`?code=BK9999` → 改写为 `?code=BK1283` 且与详情标题（银行）一致；裸 `/sectors` 同样补齐；合法 `?code=BK0438` 不被误改（仍为食品饮料）；点击传媒后 `?code=BK0486` 同步；`?code=BK9999&sort=name` → 只换 code、`sort=name` 保留；`history.length` 改写前后不变（真 replace）；5 次连续读 URL 稳定无循环改写，`/api/snapshot` 与历史接口各仅 1 次请求、无对 BK9999 的请求残留；控制台 0 条 error
  - 未新增组件层单测：项目 `npm test` 体系（`node --experimental-strip-types --test`）只覆盖 `src/lib` 纯函数，不为这一处引入 React 测试框架；`npm run build`（tsc 严格模式）通过、`npm test` 31 passed / 0 failed
  - 保留的既有行为（本项不改，供用户判断）：回落目标是「当前筛选/排序下列表第一项」而非恒定强度最高板块，因此 `sort=name` 时回落到「传媒」而不是「银行」（URL 与内容一致）

## 8. 收尾集成验证

- [x] 8.1 全量回归：项目根 `pytest` 全绿、`signal-hub-web` 下 `npm run build` 与 `npm test` 全绿
  - `pytest`（项目根，2026-11-04 11:35）→ **301 passed / 4 skipped / 90 subtests passed**（62～72s），无 failed；4 个 skip 均为代码里已显式标注的既有跳过（`pipeline/test_confirmed_index_signals.py` 三个 v1.0 时代用例待按 v1.1-confirmed 语义重写、`pipeline/test_io_utils.py` 一个依赖数据状态的用例），本变更未新增 skip；`test_openspec_validate`（`openspec validate --all --strict`）通过本次转绿
  - `signal-hub-web`：`npm run build`（tsc 严格模式 + vite）通过，`npm test` → 31 passed / 0 failed
  - 全量测试前后生产产物未动：`data/index-signals.json`（1,105,018 B）与 `data/sector-trends.json`（139,035 B）mtime 仍为 09:18，验证 3.4 的 `--output-dir` 隔离生效
- [x] 8.2 手动重建演练：运行 `重建信号数据.bat`（补数据模式），确认板块增量、指数与板块产物重建、告警与退出码行为符合"手动重建的补数据开关"既有要求
  - 方式：`Start-Process -FilePath 重建信号数据.bat -ArgumentList fetch -RedirectStandardInput/Output/Error` 免交互跑完整 bat，**退出码 0**
  - 输出链与预期一致：`Active algorithm: swing (index-swing6-v2.0)` → 检查最近已完成交易日 2026-11-03（口径 calendar）且本地 指数/板块/机构 均已覆盖 → `无落后数据，跳过拉取`（盘中不产生多余网络请求，也不会把当日未完的 bar 写入）→ `已校验并写入` 两份产物 → `[OK]` 与「运行中的服务 30 秒内拾取新文件，无需重启」
  - 重建可重复（幂等）：重建前后两文件 sha256 前 16 位与字节数完全一致（`3d049072ae2dc322` / 1,105,018 B、`f6239a49f6feecaf` / 139,035 B）
  - 告警：stderr 仅 6 条既有 `confirmed_sector_trends.py:53` UserWarning（研究段之前的 OHLC 异常 1/2 行，不影响计算），无报错无中断
- [x] 8.3 在本文件备注区记录：实际 31 个 BK 代码与名称快照、板块对照报告关键数字、指数信号对照与重验结果（含样本量）、`center-sector-detail-dialog` 处置结果（用户已定为不动），供归档时写入口径说明
  - 已写入文末「## 备注（换源留痕）」小节

## 备注（换源留痕，2026-11-04，供归档写入口径说明）

- **31 个板块清单快照**（`pipeline/fetch_market.py::EM_SECTOR_UNIVERSE`，2026-11-04 逐条校验通过，硬编码 BK 代码 + 每日名称校验）：
  BK0433 农林牧渔、BK1206 基础化工、BK0479 钢铁、BK0478 有色金属、BK1201 电子、BK1211 汽车、BK0456 家用电器、BK0438 食品饮料、BK0436 纺织服饰、BK1212 轻工制造、BK1216 医药生物、BK0427 公用事业、BK1210 交通运输、BK1202 房地产、BK1213 商贸零售、BK1214 社会服务、BK1283 银行、BK1203 非银金融、BK1217 综合、BK1208 建筑材料、BK1209 建筑装饰、BK1200 电力设备、BK1205 机械设备、BK1204 国防军工、BK1207 计算机、BK0486 传媒、BK1215 通信、BK0437 煤炭、BK0464 石油石化、BK0728 环保、BK1035 美容护理
- **数据口径**（固定不得随意更换）：板块与指数宽度均为**东财行业板块日线、不复权**，目录 `data/raw/sectors-em-industry/`（31 个 BK 文件）；盘中与收盘同源（`server/paths.SECTOR_EM_RAW == fetch_market.EM_SECTOR_RAW`，已由单测固化）；`algorithm_version` 板块 `sector-donchian20-ma60-v2.0-em-source`、指数 `index-swing6-v2.0`（未变）；产物 `data_source=东财行业板块`；旧申万缓存 `data/raw/sectors/` 保留但不再参与生产计算（5.2 已验证移走后重建逐字节一致）
- **板块四态对照关键数字**（`data/research/sector-source-migration-sw-vs-em.md`）：31/31 同名配对成功、无跳过、无历史不足板块；四态一致率 研究段 **70.59%**（样本 30,140 行业日，区间 2020-10-30～2024-12-31）/ 验证段 **75.46%**（样本 13,702 行业日，区间 2025-01-02～2026-11-03）；bull↔bear 硬翻转占比 研究段 5.20% / 验证段 4.25%
  - 对照归因补充（M-1，2026-11-04）：上述一致率与翻转占比**不包含**「滞后超 30 自然日清态」的影响 —— 对照脚本走 `calculate_sector_trend` 逐日序列，不经过 `calculate_sector_universe` 的清态分支；生产侧当前也无板块命中该分支（`data/sector-trends.json` 31 个板块尾日全部 2026-11-04，`data_lag` 与空 `state` 均为 0 个）。该规则作为**有意的保守降级**沿用（避免东财撤销/长停更板块用陈旧结论误导），已在 `confirmed_sector_trends.py` 注释与 docstring、以及 market-data-pipeline delta 的「数据滞后与严重滞后清态」场景显式声明；本次换源未收紧或放宽 30 自然日阈值
- **宽度与指数信号对照关键数字**（`data/research/breadth-source-migration-sw-vs-em.md`）：宽度输入 旧 申万一级 31 行业/6,492 行 → 新 东财行业板块 31 行业/6,500 行；breadth20 有效样本 2,928 行业日（2014-03-20~2026-11-03）有向均值 +0.0270、绝对差均值 0.0907、P90 0.2258，最近 250 日绝对差均值 0.0961；breadth60 有效样本 2,679、绝对差均值 0.1021；顶底信号旧 **73** → 新 **81**（日期一致 45、新增 36、消失 28）
- **换源后准确率重验**（未调任何阈值与权重，跨 7 指数合并，未决不进入分母，报告均已标注小样本）：研究段 bottom 24/24 → **28/28**（均 100%，新 Wilson95 [0.879, 1.0]）；研究段 top 21/22（95.45%）→ **28/28**（100%，触发 32/已判定 28/成功 28/失败 0/未决 4）；验证段 bottom 10/13（76.92%）→ **11/13**（84.62%，Wilson95 [0.578, 0.957]）；验证段 top 8/8 → **8/8**（100%，Wilson95 [0.676, 1.0]）。样本量仅 8–28，**不能据此得出「新源更准」的结论**，仅作口径变更记录在案
- **旧口径留痕**：换源前指数信号 `.backup/2026-11-03-板块换源/index-signals-换源前-旧breadth.json`；换源前受影响文件与产物同目录备份（项目无 git，该目录即回滚依据）
- **`center-sector-detail-dialog` 处置**：用户 2026-11-03 定为「先放着不动」——不归档、不废止、不修改，本 change 未触碰其 tasks/specs
- **未纳入本 change 的已报告事项**：① ~~`重建信号数据.bat` 的 fetch 模式提示文案仍称板块为「SW sector bars …trail by 1 day」~~（**已修**：2026-11-04 用户批准「全部收尾」后属 L-2 处置范围，文案已改为东财板块收盘后即定稿、指数晚间补齐，见下方「审查处置」）；② 板块页默认回落项为「当前筛选/排序下列表第一项」，因此 `sort=name` 时回落到传媒而非强度最高的银行（URL 与内容已一致，仅产品语义可选项，未改）

## 审查处置（2026-11-04，代码审查后用户批准的 4 项修复）

审查方式：CodeReview 子代理对本变更全部改动文件直读审查（项目无 git，按文件清单给定范围）。结论：**0 阻断 / 1 高（H-1）/ 3 中（M-1、M-2、M-3）/ 3 低（L-1、L-2、L-3）/ 2 建议（S-1、S-2）**。用户批准：H-1「两处都改」、M-2「改 spec 措辞」、M-1「声明为有意保守化」、归档前收尾「全部收尾」。

- **H-1（高）全量终点与增量自愈**：
  - `pipeline/fetch_market.py`：`fetch_sectors()` 默认终点由 `date.today()` 改为 `last_completed_trading_day()`（与增量同一收盘口径，避免盘中初始化把当日未完成 bar 写入缓存）；`sector_request_start()` 由「尾日+1」改为**起点含尾日**，每轮重取最后一根 bar
  - `pipeline/bootstrap.py`：新增 `_frame_values_equal()` 逐值比对，`update_sectors()` 的写盘判定由「行数与尾日未变即跳过」改为「行数相同**且逐值一致**才跳过」——否则含尾日重取的修正值会被静默丢掉（该连带改动属用户批准的 H-1 意图内）
  - 新增用例：`test_fetch_market.py::FetchSectorsEndpointTest`（3 个：默认终点走日历、显式 `end_date` 走回填、清单校验失败不落盘）与 `test_bootstrap_sectors.py::TailRevisionTest`（2 个：尾日被修正必须重写、逐值一致不重写）；原窗口用例按含尾日语义重写
  - **残留缺口（已向用户报告，未擅自扩大改动）**：当 `tail >= expected`（尾日已到最近已完成交易日）时仍走「已覆盖不请求」守卫，所以收盘刚过的那一轮不会重写当日 bar；修正值在**下一交易日的增量轮次**自愈。已在 delta 「已覆盖不请求」场景补上 AND 子句写明该自愈路径
  - **同类未改**：`fetch_indexes()` 的全量终点仍用 `date.today()`，属同类问题；但 `update_indices()` 每轮无条件重拉当年数据，故可自愈，且指数侧不在本变更任务清单内，只报告不改
- **M-2（中）两份产物 `as_of` 分叉**：按用户选定「改 spec 措辞」处理（方案 A：保留板块自身 `as_of`，不改索引侧口径）。`specs/index-signal-engine/spec.md` 的 MUST 已重写为：允许板块 `as_of` 领先指数 `as_of` ≤ 1 个交易日，超过则视为发布节奏异常需核查；两份产物与界面 MUST 各自显式标注 `as_of`；新增场景「板块时点领先指数时点」，并把「同批重建」场景的「行业集合与数据时点一致」改为只硬性要求**行业集合一致**。同时修正 5.2 的任务描述与记录（见上方）
- **M-1（中）滞后清态规则迁移声明**：`pipeline/confirmed_sector_trends.py` 在 `LAG_LIMIT_DAYS` 与 `calculate_sector_universe()` docstring 写明该规则自换源前沿用、属有意的保守降级、只作用于最新条目不进信号计算；delta `market-data-pipeline` 的「板块条目字段完整」补「数据滞后与严重滞后清态」场景与降级声明；对照归因已补充在上方备注（一致率不包含该规则影响，生产当前 0 个板块命中）
- **L-1（低）schema 顶层拒收旧键**：`pipeline/schemas.py` 的 `SECTOR_SCHEMA` 加 `additionalProperties: false`（与 `INSTITUTION_SCHEMA` 一致），旧产物顶层键（`subsector_evaluation` 等）在校验阶段即被拒；`pipeline/test_io_utils.py` 新增 `test_legacy_top_level_keys_are_rejected`（4 个旧键逐一注入均拒，并断言原载荷仍合法）
- **L-2（低）残留「申万/SW」文案 3 处**：① `重建信号数据.bat:95-96` 改为东财板块收盘后即定稿、指数晚间补齐，并明说两份产物可能差 1 个交易日；② `pipeline/swing_indicators.py::breadth_table()` docstring 改为「行业板块（东财行业板块单层清单）」（算法本体一行未动）；③ `pipeline/factor_survey.py` 两处调研文案（Zweig 宽度推力 availability、新高新低家数 hypothesis）改为东财行业板块，已重跑 `python pipeline/factor_survey.py` 刷新 `data/research/external-signal-survey.{md,json}`（两文件已无「申万」字样，生产信号未受影响）
  - 全仓扫描结果：其余「申万」提及均为**旧口径基线的正当描述**（`sector_source_survey.py`/`sector_source_migration.py`/`breadth_source_migration.py`/`research_swing_factors.py` 的报错文案等），未改；`README.md` 另有把板块说成申万旧口径的文案，超出用户批准的 3 处范围，已先报告 → 用户 2026-11-04 选定「现在改 README」，处置见下方「README 口径同步」
- **L-3（低）openspec 工件与实现对齐**：已改 `tasks.md` 2.1/2.2/2.3/3.2 与 `design.md` 3/4/9 的目录名（`data/raw/sectors-em-industry/` + `sectors-em-industry-manifest.json`）、函数名（`check_sector_universe()`）与增量起点语义（含尾日）；`market-data-pipeline` delta 的「板块趋势产物契约」澄清了版本号语义：判定规则本体与 `v1.0` 一致，而算法版本号标识 MUST 随换源升为 `sector-donchian20-ma60-v2.0-em-source`（与 `ALGORITHM_VERSION` 和 schema const 一致）
  - **未能完成的一项**：delta 里 MODIFIED 需求的场景标题（「一级板块条目完整」/「二级评估随快照返回」等）尝试去「一级/二级」后，`openspec validate --all --strict` 直接报错（MODIFIED 块的场景标题必须与现行主 spec 一致，改名会被当作“旧场景丢失 + 新增未知场景”）。已全部回退，并验证回到 12 passed / 0 failed；标题重命名应放到 `openspec-sync-specs` 阶段随主 spec 一并更新
- **M-3（中）改前副本盘点与回滚清单**：新建 `.backup/2026-11-03-板块换源/回滚清单.md`。结论：有改前副本可直接覆盖回滚的仅 **6 个代码文件 + 4 份 JSON + 1 份旧 manifest**；另有 **15 项被改文件无改前副本**（11 代码/前端 + 4 测试脚本类，只能按主 spec / archive delta 原文语义改回）、**3 类为本变更新增**（回滚时直接删）。清单同时附注：**信号历史不可无损回滚**（换回旧宽度等于换回整套信号历史，AGENTS.md 第 5/9 条）
- **S-1/S-2（建议，未处理）**：校验时机前置、全量采集路径缺测试（S-2 已由本次 `FetchSectorsEndpointTest` 部分覆盖）——属审查建议而非用户批准需求，除已做部分外未自行扩大改动
- **回归（2026-11-04 16:55 前后）**：项目根 `pytest` → **307 passed / 4 skipped / 94 subtests**（无 failed；4 个 skip 仍为既有显式跳过）；`signal-hub-web`：`npm run build` 通过（tsc 严格模式）、`npm test` → **31 passed / 0 failed**；`openspec validate --all --strict` → **12 passed / 0 failed**；`build_dashboard.py --output-dir` 向临时目录重建的两份产物与生产文件**逐字节一致**（`21d8a3518a7e973b` / 1,106,568 B、`6441f199130c6328` / 139,047 B），证明 L-1 收紧与 H-1 改动不改变输出；全量测试后生产文件 mtime 仍为 15:01（服务收盘刷新时刻），未被测试改写
- **审查后新发现（未改，仅报告）**：指数侧 `sh000905`（中证500）的 11-04 bar 成交量仅为前日 7.0%、成交额 6.3%，属同花顺收盘瞬间未聚合的残缺 bar；与本次换源无关（同花顺指数链路既有行为），`update_indices()` 晚间重拉会自愈，但若恰好在未聚合时刻做收盘重建，该日宽度会瞬时偏低

## README 口径同步（2026-11-04，用户批准「现在改 README」）

只改文档，未动任何计算代码与产物。陈旧范围大于 L-2 初次报告的 4 处，实际 9 处（均为同一件事：把板块写成申万旧口径）：

- `数据与刷新机制` 表格：`申万板块日线（当晚甚至次日上午才发布）` → `板块日线（31 个东财行业板块，不复权）/ 收盘后即发布 / 起点含本地尾日`；指数行补上「部分指数要晚间才聚合完整」
- 实测段落：删除旧结论「板块常态落后指数 0–1 日」与「**不换源**」（含旧调研数字 14973 行业日 / 24.7% / 4.0%），换为 M-2 口径（板块 `as_of` 允许领先指数 `as_of` ≤ 1 交易日、两份产物各自显式标注、超过则核查）与本次换源的带样本量对照数字（研究段 70.59%/30,140 行业日、验证段 75.46%/13,702 行业日、bull↔bear 5.20%/4.25%；宽度换源后顶底信号 73 → 81、日期一致 45），文件指向改为两份 `*-migration-sw-vs-em.md`
- 目录清单：`fetch_market.py` 注释改「同花顺指数 / 东财行业板块」，补上 `sector_source_migration.py`、`breadth_source_migration.py` 两行
- 接口表：`单板块历史序列（一级或二级）` → `（31 个东财行业板块之一，凭 code 定位）`
- `板块趋势口径` 整节重写：新增清单/数据源/盘中收盘同源条；算法版本号 `v1.1-independent-subsectors` → `v2.0-em-source`（附注规则本体与 `v1.0` 一致）；删除「一级与二级各自独立判定 / 131 个二级板块中 19 个与父级不同」；新增「滞后与展示降级」条（30 自然日阈值沿用、未收紧未放宽）；`有向强度` 与旧产物备份说明逐字保留
- 板块回归对照段：如实写明 `compare_snapshot.py` 的板块基线仍是旧申万两层结构、与单层产物不可比（见下方新发现 ②）
- 信号算法节宽度描述：「申万一级行业」→「31 个东财行业板块」
- `数据口径与约束`：补「2026-11 那次板块换源即走了单独评审与新旧对照留痕」；`data/raw/` 体积由旧的「约 45MB」改为实测（生产使用约 23MB：cffex 10.9 + sectors-em-industry 9.1 + 指数等；另有约 41MB 旧申万缓存仅留痕、已不参与计算）
- 常见问题：「7 个指数 + 162 个板块」→「7 个指数 + 31 个行业板块」；**删掉了无法当场复核的两个耗时数字**（旧文「约 1-2 分钟」与「板块全量比对约 10-13 分钟」针对 162 个板块，本次不做全量重拉不能编新值），改为「逐标的串行请求、耗时主要取决于网络」+ 保留 `GET /api/health` 的 `stage`/`detail` 查看途径

### README 改后新发现（已逐条处置，见下方「追加范围」）

1. **规格/实现漂移（→ 已按「sync 时改措辞」处置，见 9.2）**：主 spec `openspec/specs/live-data-api/spec.md:142` 与本变更 delta 同口径要「板块评估 MUST 按四态分别给出触发数、已判定样本数与胜率，MUST NOT 只输出单一百分比」，但 `build_sector_evaluation()` 与生产 `sector-trends.json` 的 `evaluation.segments[*].horizons["10"/"20"]` 实为**按段聚合的单一对象**（无四态拆分），前端 `types.ts` 仅 `evaluation?: unknown` 且无消费者。溯源：主 spec 原文的「按四态分别」本就只限定**二级板块评估**（L142：「二级部分 MUST 按四态分别…」），二级评估被本变更 REMOVED 后，delta 把该子句误 generalize 到了存活的板块级评估上——属 delta 措辞缺陷，实现从未变
2. **`compare_snapshot.py` 仍按旧两层结构写（→ 用户批准「本 change 内改为单层」，见 9.1）**：`_sector_map()`/`compare_sectors()` 使用 `item.get("parent")`、`item.get("subsectors")`、“一级板块必须零变化”，而基线 `data/research/sector-trends-baseline.json` 是 `v1.0` 两层产物（as_of 2026-10-30、31 个一级 + 二级）——与新单层产物 key 集合必不一致，命令会直接报「板块集合不一致」后返回。该脚本属开发验证工具，不生产任何信号

## 追加范围（2026-11-04 用户批准的三项收尾处理）

- [x] 9.1 `pipeline/compare_snapshot.py` 板块比对单层化：`_sector_map()` 改为以 BK `code` 为锁（不再读 `parent`/`subsectors`、不再输出 `level`）；`compare_sectors()` 返回值由 `(problems, changes)` 改为 `problems`——单层后所有板块同级，任何 state/strength/days/分组差异均算回归失败（原「二级变化只记清单不算失败」的第二通道随之删除）；新增重复 code 对账（条目数 vs 唯一 code 数）；`--baseline-sectors` 默认值由旧两层基线改为生产 `data/sector-trends.json`（与指数侧 `--baseline` 同一口径：现场重建 vs 已落盘产物），保留「文件不存在则跳过板块回归」（存档工件里的 `__skip_sectors__.json` 用法仍可用）；**旧两层基线不删不改**，仅作换源前留痕（AGENTS.md 第 9 条）
  - 新增 `pipeline/test_compare_snapshot.py`（`SectorCompareTest` 8 用例 / 4 subtests）：一致通过、as_of 不一致、state/strength/days/分组逐条变化、4 位小数容差、集合不一致早返回、重复 code、未分类无 state 不误报、旧 `subsectors` 不再进入回归口径
  - 实测（2026-11-04 17:15）：`python pipeline/compare_snapshot.py` → **PASS：信号事实与基线完全一致**（指数 7 个、已确认信号 82 条、候选 0 条；板块部分无告警，即现场重建 `build_sectors()` 的 as_of 与 31 个板块事实与生产文件逐条一致）；脚本只读不写，两份生产产物 mtime 未变
  - `README.md` 同步：板块回归段改为单层口径与新命令，`准确率披露` 段补注“四态拆分属后续单独变更”
- [x] 9.2 板块评估的规格措辞按实现修正：delta `specs/live-data-api/spec.md` 的「快照携带板块评估」需求体重写为——评估 MUST 按研究段/验证段 × 10/20 日给出触发条目数、已判定样本数、成功数、未决数、胜率与 Wilson 95% 区间且 MUST 携带说明文字，胜率 MUST NOT 以不带样本数的单一百分比呈现（保留 AGENTS.md 第 8 条的实质约束）；同时明说板块级评估口径为**该段全部板块的聚合方向胜率**，原「按四态分别」属被移除的二级评估，板块级四态拆分**不在本变更范围**（用户选定：如需则另开变更补实现——已由 `sector-eval-by-state` 补实现，2026-11-04：板块级评估改为按四态分组，四态样本之和恒等于原聚合样本）；场景标题按 `--strict` 约束保持与主 spec 一致，重命名放到 sync-specs 阶段
- [x] 9.3 H-1 残留缺口——用户选定**保持现状（次日自愈）**，不改守卫：17:07 实测当时当日（15:01 那一轮写入）与刚刚重取的 3 个板块×3 天 close/volume/amount **完全一致**，说明东财板块收盘瞬间未定稿的现象本日未出现（本日出现的是指数侧 sh000905）；且板块四态/有向强度/喂给指数的宽度均只用收盘价，产物 `series` 也只含 close+state，所以该缺口**不影响任何信号数值**，影响面仅限本地 CSV 当日量额与未来基于板块量的因子；自愈路径已写在 delta「已覆盖不请求」场景的 AND 子句里（同花顺→东财指数侧 `update_indices()` 每轮无条件重拉当年数据，不存在同类缺口）

## sync-specs（2026-11-04 用户选定「先 sync 再归档」）

- [x] 10.1 `openspec/specs/sector-trend-explorer/spec.md`：Purpose 改写为单层 31 行业口径；ADDED「单层行业板块列表与导航」；删除 4 个二级相关需求（含二级详情弹窗）；「摘要字段完整」的 WHEN 改为「用户查看任一板块详情」
- [x] 10.2 `openspec/specs/index-signal-engine/spec.md`：MODIFIED「横截面宽度」改为东财单层 31 行业 + 同源约束并新增场景「宽度与板块同源」；ADDED「横截面宽度换源同步重建与对照重验」（含 as_of 分叉说明与 5 个场景）
- [x] 10.3 `openspec/specs/live-data-api/spec.md`：MODIFIED「快照板块条目字段」「快照携带板块评估」「板块历史查询」；4 个场景改名（板块条目完整 / 板块独立状态 / 板块评估随快照返回 / 凭代码查询任一板块）
- [x] 10.4 `openspec/specs/market-data-pipeline/spec.md`：5 个 MODIFIED（数据源与口径固定、原始数据补齐与复用、板块趋势产物契约、板块条目字段完整、变更留痕与对照）+ 3 个 ADDED（东财行业板块生产清单、板块日线单一来源与宽度输入、板块日线增量与收盘定稿采集）+ 2 个 REMOVED（二级板块独立判定、二级板块独立状态评估，后者的「数据滞后」场景按 delta 声明迁至「板块条目字段完整」）
- [x] 10.5 delta 侧同步改名：因主 spec 场景标题已去掉「一级/二级」字样，本变更 delta 的 MODIFIED 块同步改名 9 个场景（live-data-api 4 个、market-data-pipeline 5 个），否则 `--strict` 会判为「遗漏现有场景 + 新增陌生场景」
- [x] 10.6 校验：`openspec validate --specs` **10 passed / 0 failed**；`openspec validate --all --strict` **12 passed / 0 failed**

### sync 阶段只报告、未擅自改的两项

1. **主 spec 残留旧口径**：`openspec/specs/market-data-pipeline/spec.md`「板块数据源候选调研留痕」正文仍写「在用口径（申万行业指数）」。本变更的 delta 未覆盖该需求，按「只做用户提的需求」未动——换源后该措辞已失真，是否修正请用户定夺（该条同时声明调研不得改变生产板块源，与本变更不冲突）
2. **跨 change 影响**：活跃 change `center-sector-detail-dialog` 的 delta MODIFIED「二级板块详情弹窗」，该需求已随本变更从主 spec 移除，`openspec` 报 INFO「Archive would refuse this delta: … 二级板块详情弹窗 - not found」——该 change 归档时会被拒。用户此前已定「先放着不动」，故本阶段未处理，仅留痕
