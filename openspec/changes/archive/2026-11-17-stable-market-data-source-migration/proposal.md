# Proposal

## Why

2026-11-12 起东财 push2 接口持续限流，板块清单校验与 ETF 清单校验连续失败，【板块趋势】与【ETF 放量】停在 `2026-11-11`；【指标总览】属另一条链路，因同花顺 `zs_1B0852` 返回 502 且 `build_indices()` 以 `min(ends)` 取 `as_of`，停在 `2026-11-12`（最近已完成交易日为 2026-11-13）。2026-11-14 用户执行「重建信号数据.bat」选项 1 并重启项目后数据仍未前进：选项 1 是 `rebuild only`（仅用磁盘 CSV 重算、不联网），11:27 那次重建只把三份产物的 `generated_at` 刷到 `2026-11-14T11:27:17`，`as_of` 全部未变；脚本本身也写明服务 30 秒内自动读盘、无需重启。

现有防护不足以恢复：`fab2894` 的分批与 30 分钟冷却只覆盖板块线，ETF 线不检查冷却；`etf-volume-events.json` 全项目仅由 `build_dashboard.main()` 写入，scheduler 的收盘重算与晚间补齐只落盘 `index-signals.json`、`sector-trends.json` 两份；`market-data-pipeline` 规格明确要求「ETF 数据线 MUST NOT 参与新鲜度判定」。因此限流解除后，ETF 页面仍不会自动前进。

项目 2026-11-04 刚因「申万接口不稳定」从申万换到东财，`data/research/sector-source-migration-sw-vs-em.md` 记录的代价是四态一致率 70.5%（研究段，30140 行业日）/ 75.5%（验证段，13702 行业日），bull↔bear 翻转 5.2% / 4.25%。更换主数据源等于更换一套信号历史，属 `AGENTS.md` #5 的价格口径变更。用户在 2026-11-14 逐项确认后授权本变更：**先测试是否存在可靠稳定的免费公开源，有则直接更换主源；全量重拉并重算；分组请求避免瞬时并发；定时任务随之调整；其他电脑按 git pull + 重启验收**。该授权即 #5 要求的「先向用户说明影响」，留痕于本提案与 `design.md`。

## What Changes

- **BREAKING** 新增候选数据源稳定性测试与选型门槛：只评估免费公开源（含「沿用东财但换接口/请求通道」与其他稳定源），按可量化的成功率、限流率、延迟、日线定稿时刻与口径可比性给出对比报告；报告 MUST 带样本量与统计区间，未达门槛 MUST NOT 切换。
- **BREAKING** 测试通过后更换**板块日线与 ETF 日线**两条生产价格口径的主数据源；新源 MUST 提供与现行 31 个单层行业板块、6 只核心 ETF 一一可比的清单，复权口径（板块/ETF 不复权）、字段顺序与价格单位 MUST 保持不变。**指数日线（同花顺、前复权）本次不换源**：其问题不是限流，测试阶段只做稳定性观测，是否更换须在结果出来后单独确认。
- **BREAKING** 全量重拉并重算：新源覆盖 `2020-10-30` 起完整历史，重算指数信号、板块趋势与 ETF 放量事件；保留切换前的原始日线、manifest 与三份产物作为对照基线，输出逐板块/逐 ETF 的换源对照与新旧准确率对比（样本量、统计区间、Wilson 区间齐备），算法版本号与数据源标识随换源升版。
- 新增跨数据线统一分组请求治理：板块 31 个、ETF 6 只、各类清单校验共用同一限速队列与冷却状态，按批次串行并控制请求间隔，MUST NOT 在同一时刻并发打多个东财/同花顺接口；限流识别即止损并全局冷却，MUST NOT 反复重试延长封禁。
- **BREAKING** ETF 数据线纳入新鲜度判定与自动重建：scheduler 的收盘重算、晚间补齐 MUST 落盘三份产物；ETF 落后 MUST 触发补齐并在健康检查中可见。
- 修正【指标总览】的滞留成因：为单只指数拉取失败增加独立补偿重试；统一收盘/盘中 `as_of` 口径（现为 `min(ends)` 与 `max(history[-1])` 不一致），并在产物中显式给出各指数尾日，避免一只短板无声拖住整页。
- 手动重建入口增加可观测性：选项 1 MUST 明确提示「不联网、不会改变数据所属交易日」，并在完成后输出三份产物的 `as_of` 与落后交易日数；默认行为与「不联网」的安全语义保持不变。
- 定时任务随源调整：收盘重算与晚间补齐时刻按新源实测定稿时间重新校准；健康检查 MUST 暴露调度版本/数据源版本与各数据线 `as_of`，供多机验收。
- 多机更新按 `git pull` + 重启服务验收：定时任务逻辑全在源码中、开关为进程内状态（重启恢复全开），**不存在需迁移的落盘调度配置**；提案须给出同步前自检（工作区未提交改动会使 `--ff-only` 失败）、`data/raw/refresh-state.json` 本机台账跨机器覆盖的处理，并修正 README 中「data/raw/ 不入库」与实际全量入库口径的矛盾。

**非目标**：不引入备用源自动兜底、不做并行观察期（用户已选「全量重拉并重算」）；不改动 31 个行业板块与 6 只 ETF 的生产清单；不改动四态判据、有向强度公式、六方向权重与三层确认机制；不改动前端配色与页面结构；不更换机构持仓（中金所）与龙虎榜数据线。

## Capabilities

### New Capabilities

- `market-data-source-evaluation`: 候选免费公开源的稳定性测试口径、可比性门槛、测试报告留痕与「未达标不切换」的判定契约。

### Modified Capabilities

- `market-data-pipeline`: **BREAKING** 放开「价格口径固定为同花顺/东财」的锁定为「经测试与单独评审后可更换」；新增跨线统一分组限速、全量重拉与新旧口径对照、板块/ETF 换源后的单一来源与留痕；**BREAKING** 撤销「ETF 数据线不参与新鲜度判定」；手动重建入口增加 `as_of` 与落后天数输出。
- `live-data-api`: **BREAKING** 收盘重算与晚间补齐从落盘两份产物改为三份；补齐时刻按新源定稿时间可配置；健康检查暴露数据源/调度版本与各数据线新鲜度；`as_of` 口径在收盘与盘中模式统一。
- `etf-volume-events`: **BREAKING** ETF 日线来源由「固定东财 ETF 历史日线接口」改为「经测试与评审后的在用主源」；换源 MUST 输出新旧放量事件对照并保留旧口径历史结论。
- `index-signal-evaluation`: 换源 MUST 输出新旧口径下的信号/准确率前后对比，逐项给出触发次数、已判定样本数、Wilson 95% 区间与统计区间，并标注该对比为口径变更对照而非算法改进。

## Impact

- **数据口径与产物**：`data/raw/sectors-em-industry/`、`data/raw/etf-daily/` 及两份 manifest 全量重拉；`data/index-signals.json`、`data/sector-trends.json`、`data/etf-volume-events.json` 重算；`pipeline/schemas.py` 的数据源标识与算法版本校验需同步；旧版产物移到 `data/research/` 或 `.backup/` 留痕。
- **采集层**：`pipeline/fetch_market.py`、`pipeline/fetch_etf.py`、`pipeline/bootstrap.py`、`pipeline/trading_calendar.py`；新增统一限速模块与候选源测试脚本。
- **构建与调度**：`pipeline/build_dashboard.py`（`as_of` 口径与各指数尾日）、`pipeline/refresh_and_rebuild.py`、`server/scheduler.py`（时刻常量、三份产物、限流器接入）、`server/snapshot.py`（收盘/盘中 `as_of` 统一）、`server/app.py`（健康检查字段）。
- **入口与文档**：`重建信号数据.bat`（选项 1 提示与 `as_of` 输出）、`快速同步拉取代码.bat`（同步前自检）、`README.md`（入库口径与多机更新章节）。
- **测试**：`pipeline/test_fetch_market.py`、`test_bootstrap_sectors.py`、`test_etf_volume_events.py`、`test_refresh_and_rebuild.py`、`server/test_server.py`，以及新增的稳定性测试、限速器、换源对照、三份产物落盘、`as_of` 一致性用例。
- **规则约束**：`AGENTS.md` #5（口径变更须先说明影响，已由本提案与用户 2026-11-14 确认满足）、#1 禁止未来函数、#3 只做本次需求、#6 指标可与通达信/同花顺对账、#7 调参与验证分离、#8 准确率带样本量与统计区间、#9 算法/口径变更留痕、#11 三层确认完整、#12 三层确认日志完整、#13 UI 风格一致，均为硬性验收项。
