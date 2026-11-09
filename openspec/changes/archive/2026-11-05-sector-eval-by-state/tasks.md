# Tasks

## 1. 基线备份与改动前留痕

- [x] 1.1 把生产 `data/sector-trends.json` 备份到 `.backup/2026-11-04-板块评估四态/sector-trends-改前.json`，并把改前文件的 sha256 记进本任务完成记录；验证：备份文件存在且 `Get-FileHash` 与生产文件一致
  - 已完成（2026-11-04）：备份与生产文件 sha256 均为 `6441F199130C632886F946433EBE1DED3BD48B610ACA3C1C720EA2F164EE7B77`；同时确认当时无 `python -m server` 常驻进程（仅 IDE 的 autopep8 LSP）
- [x] 1.2 记录「除 `evaluation` 外」的基线指纹（`as_of`、`algorithm_version`、`data_source`、`dates` 长度与首尾、`groups` 各态板块数、每个板块的 `state`/`strength`/`days`/`series` 摘要），供第 2 组逐条比对；验证：把指纹写进完成记录，后续比对能以它为准
  - 已完成（2026-11-04）：写入 `.backup/2026-11-04-板块评估四态/baseline-fingerprint.json`，关键值 `without_evaluation_sha256=b40ce2b365f43abac5876444b0f8a7eacde39d9fde7dec657e7916aed533430e`、`file_sha256=6441f199…`、`as_of=2026-11-04`、`dates=250（2025-10-24～2026-11-04）`、`groups: bull 2 / warn 0 / bear 27 / rise 2 / unclassified 0`

## 2. 流水线：评估按四态分组

- [x] 2.1 `pipeline/confirmed_sector_trends.py::build_sector_evaluation()` 改为 `summarize_sector_events(all_events, ["type"])`，`build_segment()` 输出 `{"segment","independent","states"}`，`states` 固定含 `bull`/`warn`/`bear`/`rise`，每态含 `"10"`/`"20"`，无事件时为 `null`；四态判据、`evaluate_sector_states()`、`ALGORITHM_VERSION` 一行不改；验证：`pytest pipeline/test_confirmed_sector_trends.py` 通过
  - 已完成（2026-11-04）：只改分组键与段内组装，`build_cell()` 负责「状态 × 窗口」取值、空值返回 `None`；判据/强度/版本号未动
- [x] 2.2 更新 `pipeline/test_confirmed_sector_trends.py` 现有三个 evaluation 用例：断言 `states` 四态齐备、每态含 10/20、历史不足时四态均为 `null`；新增一条「四态 `samples` 之和 == 全部事件已判定数、`entries` 之和 == 全部事件数」的对账用例；验证：该文件 pytest 通过且对账用例不是空断言（构造出的四态样本数均 > 0）
  - 已完成（2026-11-04）：三个旧用例改按 `states` 断言（含「不再输出聚合 `horizons`」），新增 `test_four_states_appear_in_oscillating_history`（研究段四态样本均 > 0）与 `test_four_state_samples_sum_to_all_events`（逐段逐窗口对账 `entries`/`samples`/`pending`）；`pytest pipeline/test_confirmed_sector_trends.py` → 21 passed / 18 subtests
- [x] 2.3 用 `build_dashboard.py --output-dir <临时目录>` 演练一次，核对：①四态 `samples` 之和覆盖聚合基准（研究段 10 日 5,075、20 日 5,049；验证段 10 日 2,184、20 日 2,120，若因新增交易日而变大需说明差值来源）；②除 `evaluation` 外与 1.2 的指纹逐条相等；③生产 `data/index-signals.json` 哈希前后不变；验证：三项比对结果写入完成记录，任一不满足即停下排查，不得继续重建生产
  - 已完成（2026-11-04）：①四态合计与拆分前聚合**逐项完全相等**——研究段 10 日 条目 5152/已判定 5,075、20 日 5152/5,049；验证段 10 日 2205/2,184、20 日 2205/2,120（同一批事件重新分组，无新增/丢失）；②演练产物 `without_evaluation_sha256=b40ce2b3…` 与基线一致，`as_of`/版本号/`data_source`/`dates`/`groups` 全等；③`data/index-signals.json` 哈希前后均为 `21D8A3518A7E973B9AA966B7203D8926DE832E62378894610BE48F00A8B72F3A`（演练产物同哈希）
  - 顺序说明：演练前 `protected_write` 因 schema 仍要求 `horizons` 直接拒收新结构，故先落地了 3.1 的 `pipeline/schemas.py` 改动（代码已改，该任务的验证用例与全量测试留待 3.2 重建后再跑）
- [x] 2.4 把演练得到的四态胜率表（研究段 / 验证段 × 10 / 20 日 × 四态，含已判定样本量与 Wilson 区间）提交用户核对，并说明「验证段为事后观察、不构成独立验证」；验证：用户明确回复继续后才可执行 3.2
  - 已完成（2026-11-04）：已提交完整四态表（16 格，含条目/已判定/未决/小样本列），用户回复「确认，继续重建生产」后进入第 3 组

## 3. 产物契约收紧与生产重建

- [x] 3.1 `pipeline/schemas.py`：删除 `SECTOR_EVALUATION` 里的 `horizons`，新增 `SECTOR_STATE_HORIZONS`（required `10`/`20`，`additionalProperties: false`），`segments[*]` 与 `states` 均加 `additionalProperties: false`；验证：`pytest pipeline/test_io_utils.py` 新增用例——带 `horizons` 的载荷被拒、缺某一态的载荷被拒、合法四态载荷通过（用例在内存构造载荷，不依赖生产文件）
  - 已完成（2026-11-04）：`SECTOR_HORIZON_EVALUATION` 补 `additionalProperties: false`；新增 `SECTOR_STATE_HORIZONS`、`SECTOR_EVALUATION_STATES`；`segments[*]` 的 `required` 改为 `segment`/`independent`/`states` 并加 `additionalProperties: false`。`test_io_utils.py` 新增 `SectorEvaluationSchemaTest` 四个内存构造用例（四态合法、某态 `null` 合法、旧聚合 `horizons` 被拒、缺某一态被拒）+ `SectorContractTest.test_real_evaluation_is_split_by_four_states`；`pytest pipeline/test_io_utils.py` → 31 passed / 1 skipped
- [x] 3.2 重建生产 `data/sector-trends.json`：先确认没有常驻服务或旧代码进程在跑（避免旧进程覆盖产物），再执行重建；验证：读取产物断言无 `horizons`、四态齐备、`algorithm_version` 仍为 `sector-donchian20-ma60-v2.0-em-source`、`pytest pipeline/test_io_utils.py` 通过
  - 已完成（2026-11-04）：重建前 `Get-CimInstance Win32_Process` 确认仅 IDE 的 autopep8 LSP（PID 10212），无常驻 `python -m server`；`build_dashboard.main()` 写入两个产物的日志均为「已校验并写入」。产物核对：`evaluation.segments[*]` 仅含 `segment`/`independent`/`states`（无 `horizons`）、四态齐备且每态含 `10`/`20`、`algorithm_version=sector-donchian20-ma60-v2.0-em-source`、`as_of=2026-11-04`、`dates=250（2025-10-24～2026-11-04）`、`groups` 与原一致（bull 2 / warn 0 / bear 27 / rise 2）；新 `without_evaluation_sha256=b40ce2b3…` 与 1.2 基线逐字相等，新 `file_sha256=b0c24c29c0195c87eec19e76e0363cead796723bb1f5923a0c8cbad8ef225073`
  - 四态合计复核：研究段 10 日 1416+1163+1389+1184=5152 条目 / 5075 已判定，20 日 5049；验证段 10 日 689+568+500+448=2205 / 2184，20 日 2120 —— 与拆分前聚合口径逐项相等
- [x] 3.3 核对服务端承载：`server/test_server.py` 中断言板块评估的用例按新结构覆盖（四态随快照返回、评估为 `None` 时仍返回占位结构）；验证：`pytest server` 通过
  - 已完成（2026-11-04）：`SectorSnapshotTest` 新增 `test_snapshot_carries_four_state_evaluation`（两段顺序、无 `horizons`、四态齐备、每格含 `rate`/`samples`/`successes`/`pending`/`wilson95`/`small_sample`、`note` 含「方向胜率不代表交易收益」）与 `test_absent_evaluation_is_not_fabricated`（评估为 `None` 时不伪造，板块列表照常返回），并在 `test_missing_payload_returns_placeholder` 补 `evaluation is None`；`pytest server` → 66 passed / 42 subtests
- [x] 3.4 更新 `README.md`「准确率披露」段：改为四态口径并给出每态胜率 + 已判定样本量 + Wilson 区间，删除「旧产物按四态给出的是二级板块评估」的悬空表述，补一句「四态样本之和等于原聚合样本，需要总体口径时可直接相加」；验证：用脚本把 README 里的数字与重建后的 `data/sector-trends.json` 逐项核对一致
  - 已完成（2026-11-04）：改写为「研究段/验证段各一张四态表（行=四态，列=10/20 日，格=胜率（样本量，Wilson 区间））」，删除二级评估悬空表述，补「四态样本之和恒等于拆分前聚合样本」与方向期望口径、未决不计入分母
  - 额外改动（经用户选定「按段改写并重建产物」）：产物 `evaluation.note` 原句「空头状态历史胜率低于50%」与四态数据矛盾（研究段 bear 53.42%、验证段 bear 43.12%/39.32%），改为「空头状态在验证段胜率低于50%，研究段约53%」；仅换说明文字、不动任何计算，重建后 `without_evaluation_sha256` 仍为 `b40ce2b3…`，新 `file_sha256=766f4b80e29767e5073980a420c7e1cbb8f1a1deb8ca57d65af5e6a301b9e467`
  - 验证：`.tmp-readme-check.py` 解析 README 两张表 16 格 + `note` 片段 + 研究段四态样本合计，输出「README 四态表与 data/sector-trends.json 逐项一致」
- [x] 3.5 更新归档变更 `openspec/changes/archive/2026-11-04-switch-sector-source-em/tasks.md` 中「板块级按四态拆分属后续单独变更（该后续变更尚未创建）」与 9.2 注记：保留历史结论，补注「已由 `sector-eval-by-state` 补实现」；验证：全文检索该文件中不再有指向「尚未创建」的表述
  - 已完成（2026-11-04）：9.2 注记内的「该后续变更尚未创建」改为「已由 `sector-eval-by-state` 补实现，2026-11-04：板块级评估改为按四态分组，四态样本之和恒等于原聚合样本」；`Select-String` 全文检索「尚未创建」无命中（README 侧的同类表述已在 3.4 删除）

## 4. 前端：四态胜率验证区块

- [x] 4.1 `signal-hub-web/src/data/types.ts` 新增 `SectorStateEvaluation` / `SectorEvaluationSegment` / `SectorEvaluation`（`states` 四态 × `10`/`20`，每格字段与产物一致，允许 `null`），把 `SectorBlock.evaluation` 由 `unknown` 收窄为 `SectorEvaluation | null`；验证：`npm run build`（tsc + vite）无类型错误
  - 已完成（2026-11-04）：新增 `SectorStateEvaluation`（`rate`/`wilson95` 允许 `null`）、`SectorEvalHorizon`、`SectorEvaluationSegment`、`SectorEvaluation`；`SectorBlock.evaluation?: SectorEvaluation | null`。`npm run build` → tsc 无错、vite 构建成功
- [x] 4.2 新增 `src/lib/sectorEvaluation.ts` 纯函数（把评估归一为「段 × 窗口」行数据、格式化胜率/样本量/Wilson 区间、判定无样本与小样本），配套 `tests/sectorEvaluation.test.ts` 覆盖四态齐备、无样本显示占位、小样本标记、区间格式；验证：`npm test` 全绿
  - 已完成（2026-11-04）：导出 `HORIZONS`/`SEGMENT_LABELS`/`groupThousands`/`formatRate`/`formatSamples`/`formatInterval`/`buildCellView`/`buildSegmentView`/`buildEvaluationViews`；行序复用 `STATE_ORDER`。测试 5 条用例（四态齐备与行序、无样本占位（`null` 与 `samples=0` 两种来源）、小样本透传、区间与千分位格式、空评估返回 `[]`）；`npm test` → 36 passed / 0 failed
  - 实现细节：`sectorEvaluation.ts` 对 `sectorStates.ts` 的运行时导入必须带 `.ts` 后缀（node `--experimental-strip-types` 不做扩展名推断），tsconfig 已开 `allowImportingTsExtensions`
- [x] 4.3 新增 `src/components/SectorEvaluationPanel.tsx`：两段各一张卡（研究段 / 验证段），卡内四行（四态，复用 `STATE_ORDER`/`STATE_LABELS`/`STATE_TEXT_CLASS`）× 两列（10 日 / 20 日），每格显示胜率 + `n=` + Wilson 区间 + 小样本标记，无样本显示 `—`，标签文案用「方向胜率」不用「正确率/胜率极高」；验证：`npm run build` 通过
  - 已完成（2026-11-04）：`HorizonCell`（胜率 + `n=` + 区间 + 小样本徽标，「小样本」文案沿用产物口径）、`SegmentCard`（段卡 + 表格）、`SectorEvaluationPanel`（标题「方向胜率验证」、算法版本号、底部说明）。四态行用 `STATE_TEXT_CLASS` 配色，风格沿用 `glass-card` / 淡色系
- [x] 4.4 在 `src/pages/Sectors.tsx` 的 `xl:grid-cols-[320px_1fr]` 网格下方挂载该面板，并保证评估为 `null`/结构为空时渲染「评估尚未生成」提示、不渲染空表；面板不接收选中板块参数（口径为全部行业板块）；验证：`$env:PORT="8765"; .\.venv\Scripts\python.exe -m server` 启动后目视核对——切换选中板块时面板数值不变、每格有样本量与区间、底部说明文案可见
  - 已完成（2026-11-04）：挂在网格外、页面底部 `<div className="mt-6">` 内，只传 `block?.evaluation`；`buildEvaluationViews` 为空时返回「评估尚未生成」卡片。8765 实测用 DOM 文本核对：点击左侧「银行」前后，面板整块 `innerText` **逐字符完全相同**；每格均有胜率 + `n=` + Wilson 区间；说明文字可见
  - 说明：面板区域截图因浏览器截图工具多次超时未能生成（页面顶部整页截图已存 `.screenshots/sectors-page.png`），核对改用 DOM 文本，结论同样可复查
- [x] 4.5 面板说明文案落地：「板块趋势为描述性分类，方向胜率不代表交易收益；验证段为事后观察，不构成独立验证」，并确认页面无「买入/卖出/可交易」类措辞；验证：检索 `Sectors.tsx` 与新增组件确认措辞，且页面截图可见该说明
  - 已完成（2026-11-04）：第一行渲染产物 `evaluation.note`（当前为「板块趋势为描述性分类，方向胜率不代表交易收益；空头状态在验证段胜率低于50%，研究段约53%，仅供参考。」），第二行固定文案「验证段为事后观察、不构成独立验证；四态样本之和等于该段全部板块事件，需要总体口径时可直接相加。」（始终可见，`evaluation` 缺失时第一行回落 `DEFAULT_NOTE`）；检索 `Sectors.tsx`、`SectorEvaluationPanel.tsx` 无「买入/卖出/可交易」（页面仅有「不构成买卖建议」的中性否定表述）；8765 实测该块 `innerText` 末两行均可见

## 5. 集成验证

- [x] 5.1 全量后端测试：`pytest pipeline server` 通过（仅允许既有的 `test_openspec_validate` 失效这一条已知例外，若已修复则要求全绿）
  - 已完成（2026-11-04）：`pytest pipeline server` → **324 passed / 4 skipped / 106 subtests**，无失败（已知例外未出现）
- [x] 5.2 产物一致性终检：生产 `data/sector-trends.json` 与 1.2 基线除 `evaluation` 外逐条相等，`data/index-signals.json` 哈希与改动前一致；验证：比对脚本输出「一致」并把哈希写进完成记录
  - 已完成（2026-11-04）：`without_evaluation_sha256=b40ce2b365f43abac5876444b0f8a7eacde39d9fde7dec657e7916aed533430e`，与 1.2 基线**逐字相等**；`as_of=2026-11-04`、`data_source=东财行业板块`、`dates=250（2025-10-24～2026-11-04）`、`groups: bull 2 / warn 0 / bear 27 / rise 2` 均与基线一致；`data/index-signals.json` 哈希 `21D8A3518A7E973B9AA966B7203D8926DE832E62378894610BE48F00A8B72F3A` 与改动前（及演练产物）完全一致；生产 `data/sector-trends.json` 最终 `file_sha256=766F4B80E29767E5073980A420C7E1CBB8F1A1DEB8CA57D65AF5E6A301B9E467`
- [x] 5.3 前端整体验证：`npm test` 与 `npm run build` 全绿；启动 8765 预览后确认板块页四态列表、详情、走势图行为未变，仅新增评估区块
  - 已完成（2026-11-04）：`npm test` → 36 passed / 0 failed；`npm run build` → tsc 无错 + vite 构建成功。8765 实测（多轮浏览器核对）：筛选按钮（全部/多头/反转/退潮/空头/未分类）、搜索框、「命中 31 个板块」均在；详情四张小卡（方向强度 7.7 / 有向（统一公式）/ 状态持续 1 日 / 数据日 2026-11-04）与「强度怎么读？」入口正常；走势图 `<canvas>` 与「覆盖 2026-05-12 ~ 2026-11-04（120 个交易日）」正常，切到第三个板块后详情与走势图均更新；console 无 error
