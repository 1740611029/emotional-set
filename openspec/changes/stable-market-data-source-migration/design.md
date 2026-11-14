# Design

## Context

排查 2026-11-14「三页数据滞留」已定位到三类不同根因（详见 proposal.md - Why），归纳出三个结构性弱点：

1. **采集层无统一限速**：`pipeline/fetch_market.py` 的限流冷却是东财板块线私有的进程级全局 `_em_rate_limited_until`（第 90–107 行），ETF 线（`fetch_etf.update_etf_daily`）与指数线完全不受其约束；`update_sectors()` 在冷却中整轮跳过（第 213–217 行），但 ETF 清单校验仍会在冷却期内另起请求。各数据线「分组/分批」节奏散落在 `SECTOR_*` 常量（第 38–41 行）与 `fetch_etf` 内部，没有统一队列，瞬时并发会叠加触发限流。
2. **调度层漏接 ETF 线**：`bootstrap.refresh_daily()`（第 311–326 行）只调 `update_indices` / `update_sectors` / `update_institutions`，**根本不调 `update_etf`**；`_backfill_blocking()`（scheduler.py 第 123–129 行）与 `_tick_close()`（第 286–290 行）重算只落盘 `index-signals.json` 与 `sector-trends.json` 两份；`data_gap()` / `latest_local_dates()`（bootstrap.py 第 352–371 行）也不含 `etf`。所以即使原始 ETF CSV 补齐，`etf-volume-events.json` 也不会自动前进——产物唯一的写入点是 `build_dashboard.main()`，只在手动「重建信号数据」时触发。
3. **as_of 口径分裂且单只失败无补偿**：`build_indices()` 用 `min(ends)`（7 指数取最早），`build_live_snapshot` 用 `max(history[-1]["date"])`（snapshot.py 第 317 行附近），同一批数据在收盘/盘中模式会差一个交易日；`update_indices()` 单只失败只 `continue`（第 67–70 行），无独立补偿重试，一只 502 就把整页 `as_of` 拖住且无可见短板提示。

定时任务逻辑全部写在源码（`server/scheduler.py` 的 `CLOSE_HOUR_MINUTE`、`EVENING_SLOTS`、`SERVER_TASKS` 常量）与进程内内存开关（`task_enabled`，重启恢复全开，scheduler.py 第 64–65 行），**不存在需独立迁移的落盘调度配置**。这决定了「其他电脑如何更新」的答案：git pull + 重启服务即生效，不需要迁移文件。

本次变更在 `market-data-source-evaluation` 的测试契约约束下，**先验证是否存在可靠的免费公开源**，验证通过后才换板块/ETF 两条生产价格口径主源；指数日线（同花顺、前复权）问题属另一条链路，本次不换源、只修 `as_of` 与补偿重试。

## Goals / Non-Goals

**Goals:**
- 用可量化、带样本量与统计区间的实测，决定是否存在可替代的稳定免费源；门槛在测试前冻结。
- 在测试达门槛后，把板块日线与 ETF 日线换到在用主源，全量重拉 `2020-10-30` 起历史并重算三份产物，旧口径基线可回溯。
- 把分散在各 fetch 模块的分批/间隔/冷却收敛为**一个跨数据线的统一限速队列**，限流识别即止损、冷却覆盖同发行方全部线（含 ETF 清单校验）。
- 把 ETF 数据线接入新鲜度判定与自动重建（收盘重算、晚间补齐、手动重建三处都落盘第三份产物）。
- 统一收盘/盘中 `as_of` 导出规则，为单只指数失败增加独立补偿重试，产物与健康检查显式给出各指数尾日与落后标的。
- 把定时任务时刻按新源实测定稿时间重校准，健康检查暴露数据源/调度版本与各线 `as_of`，多机可直接比对。
- 给出「git pull + 重启」的多机更新验收步骤，并修正 `refresh-state.json` 跨机覆盖与 README 入库口径矛盾。

**Non-Goals:**
- 不引入备用源自动兜底（用户已选全量重拉并重算，不做并行观察期）。
- 不改 31 个行业板块与 6 只 ETF 的生产清单、破百亿阈值、四态判据、有向强度公式、六方向权重、三层确认机制、前端配色与页面结构。
- 不更换机构持仓（中金所）与龙虎榜数据线。
- 不在本次决定指数日线是否换源；指数源的测试观测结论只作为后续单独确认的输入。

## Decisions

### D1. 统一限速模块 `pipeline/rate_limiter.py`

新建跨数据线共享的限速器，替换 `fetch_market.py` 的 `_em_rate_limited_until` 私有全局：

- **队列化串行**：所有外部行情取数（指数/板块/ETF 的清单校验与日线）在入站前经 `limiter.acquire(issuer)` 取令牌，令牌按批次放出；批大小 `BATCH_SIZE`、批间 `BATCH_PAUSE`、单请求 `REQUEST_GAP` 同为可配置常量，运行前声明。任意时刻不允许多个请求并发打不同接口。
- **按发行方冷却**：`issuer` 抽象为 `eastmoney` / `ths` 等。某发行方任一线触发限流 → `mark_limited(issuer)` 进入全局冷却（沿用 30 分钟口径），冷却覆盖该发行方全部数据线，含 ETF 清单校验。冷却期内 `acquire` 立即返回「跳过」而非重试。
- **识别即止损**：第一批请求返回限流即中止本轮剩余请求，不再对该批标的重试；避免 `call_with_retry` 当前「4 次重试叠加延长封禁」的问题（fetch_market.py 第 63–83 行）。
- **可观测**：`limiter.snapshot()` 返回各发行方冷却剩余秒数与跳过的数据线，供健康检查与手动重建入口读取。

**备选**：保留各 fetch 模块各自的常量与私有冷却。否决——正是这种分散导致了 ETF 线在板块冷却期另起请求、且指数线完全无冷却约束。

### D2. 候选源稳定性测试（新 capability 落地）

- 新增 `pipeline/test_source_stability.py`，产出落 `data/research/source-eval/`（独立研究目录，**不碰** `data/raw/*` 与三份生产产物，`market-data-source-evaluation` 的「测试不触碰生产」场景约束）。
- 候选清单同时登记两类：① 同一发行方的其他接口/请求通道（如东财不同 akshare 接口、不同 push2 通道）；② 其他发行方的免费公开源（如新浪、腾讯、交易所公开接口的 akshare 封装）。需凭据的源直接排除并记原因。
- 观察窗口与门槛在运行前冻结为 JSON（成功率下限、限流率上限、延迟上限、日线定稿时刻要求、口径可比性要求），测试后不得调整。
- 每条受影响数据线分别重复取数，记录总请求/成功/失败、延迟分位、当日日线首次可获取时刻、限流恢复时长；比率必带样本量与统计区间。
- 口径可比性门槛：板块须与固化 31 个单层行业一一对应（BK 代码主键，fetch_market.py 第 48–60 行）、ETF 须与核心 6 只逐一对应，复权/字段/单位不变；缺项即判不可比，禁止按名称近似匹配。
- 报告作为独立研究产物；删除后重建生产产物须与删除前逐条一致（`market-data-source-evaluation` 的「报告不进生产链路」场景）。**只有达门槛的源才进入换源实施（D3）。**

### D3. 换源实施与全量重拉

仅当 D2 达标后执行，针对板块日线与 ETF 日线：

- **基线保留**：切换前把旧源 `data/raw/sectors-em-industry/`、`data/raw/etf-daily/`、两份 manifest、切换前三份产物打包到 `data/research/source-baseline/<YYYY-MM-DD>/`，不覆盖不删除（`market-data-pipeline` 的「旧产物可回溯」场景）。
- **全量重拉**：复用现有 `ensure_raw_data()` 的全量分支，但改走新源的接口封装；从 `2020-10-30`（项目既定历史起点）起覆盖，不把新源增量拼在旧源历史之后（`market-data-pipeline` 的「全量重拉不拼接」场景）。板块/ETF 的复权（不复权）、`date/open/close/high/low/volume/amount` 七字段、`amount` 单位（元）保持不变。
- **重算与升版**：调 `build_dashboard.main()` 重算三份产物；`pipeline/schemas.py` 的数据源标识与算法版本号随换源升版，随产物输出。
- **对照报告**：输出逐板块/逐 ETF 的新旧状态与放量事件对照，并输出换源前后信号准确率对照（研究段/验证段 × 5/10/20/30 × bottom/top，逐格带触发次数、已判定样本、Wilson 95%、统计区间，见 `index-signal-evaluation`）；明确标注「口径变更对照、非算法改进」。
- **索引/机构/龙虎榜线不在此列**：其数据源不变，不参与本次换源重算。

### D4. ETF 数据线接入新鲜度与自动重建

消除「调度漏接 ETF」的结构缺口：

- `bootstrap.latest_local_dates()` 与 `data_gap()` 增加 `etf` 线，取 6 只 ETF CSV 尾日的最小齐备日（沿用 `etf-volume-events.json` 既有的 `build_etf_volume_events()` 口径）。
- `refresh_daily()` 在 `update_institutions` 之后补调 `update_etf()`，并把 ETF 纳入返回字典的 `refreshed`。
- `server/scheduler.py` 的 `_backfill_blocking()` 与 `_tick_close()`：当 `changed` 含 `indices`/`sectors`/`etf` 任一变化时，额外 `protected_write("etf-volume-events.json", build_etf_volume_events())`。
- ETF 落后触发补齐但**不拖累**指数/板块/机构三条线的新鲜度判定（`market-data-pipeline` 的「不改变新鲜度判定」场景：三条线已覆盖时整体结果逐线列出、ETF 单标落后）；ETF 补齐失败只告警、不返回整体「数据落后」。

### D5. as_of 口径统一、单只指数补偿重试与短板可见

- `build_indices()` 保持 `as_of = min(ends)` 作为聚合口径，但同时在产物输出 `index_tails`（7 个指数各自尾日）。
- `update_indices()`（bootstrap.py 第 47–94 行）对单只失败增加**独立补偿重试**（退避 2–3 次），仍失败则标记该指数落后并 `continue`，不再「一次失败即永久跳过」；连续失败保留旧尾日、不插值（满足 `market-data-pipeline` 的「单只指数补偿重试」场景）。
- `snapshot.py` 的 `build_live_snapshot` 改用与 `build_close_snapshot` 相同的 `as_of` 导出规则，不再用 `max(history[-1]["date"])`；收盘/盘中模式不再因口径不同差一个交易日（`live-data-api` 的「收盘与盘中口径一致」场景）。
- 产物与 `/api/health` 同时给出 `as_of`、各指数尾日、落后标的代码（`live-data-api` 的「短板标的随日期一起返回」场景）。

### D6. 定时任务时刻重校准与健康检查字段

- `server/scheduler.py` 的 `CLOSE_HOUR_MINUTE`、`EVENING_SLOTS` 改为按新源实测日线定稿时刻校准并声明（D2 的「定稿时刻被记录」场景）；保留「数据未落后时补齐时刻不发请求、同一批积压时刻只执行最靠后一轮」的去重试风暴逻辑（scheduler.py 第 325 行）。
- `MarketScheduler.status()` 增加：在用主源标识与版本、调度版本 `SCHEDULER_VERSION` 常量、限速器冷却快照、每条数据线的本地 `as_of`/最近已完成交易日/落后交易日数；明确区分「实时快照可用」与「日线已跟上」（`live-data-api` 的「多机版本可比对」「各数据线新鲜度分别可见」「冷却状态可见」场景）。

### D7. 手动重建入口的可观测性

`重建信号数据.bat` 选项 1（rebuild only，不联网）在完成后输出三份产物各自的 `as_of` 与相对最近已完成交易日的落后交易日数（调用 `bootstrap.data_gap()`），明确提示「不联网、不会改变数据所属交易日」，避免「重建成功」被误读为「数据已更新」（`market-data-pipeline` 的「仅重建不联网」「不产生日期前进假象」场景）。

### D8. 多机更新与文档修正

- 定时任务逻辑全在源码、开关为进程内状态，**不存在需迁移的落盘调度配置**；验收步骤：`git pull` → 确认无未提交改动 → 重启服务 → 比对两端 `/api/health` 的 `scheduler_version` 与数据源版本一致。
- `快速同步拉取代码.bat` 增加前置自检：若 `git status --porcelain` 非空（如当前工作区未提交的 `data/etf-volume-events.json`），先中止并提示提交或 stash，避免 `--ff-only` 直接失败。
- `data/raw/refresh-state.json` 是本机台账却已入库（`git ls-files` 可查），多机 pull 会互相覆盖；方案：在 `.gitignore` 排除该台账（或文档明确「拉取后本机覆盖回本地副本」），与「仅本机状态」语义一致。
- 修正 `README.md` 中「data/raw/ 不入库」与实际全量入库的矛盾，补「多机更新」章节。

## Risks / Trade-offs

- **[风险] 候选源全部未达门槛** → 缓解：`market-data-source-evaluation` 已规定「未达标 MUST NOT 切换」，保留现用源；此时本变更只落地 D1（统一限速）、D4（ETF 接入）、D5（as_of/补偿）、D6/D7/D8（观测与文档），换源 D3 不执行。范围按达标情况收敛，不强行换源。
- **[风险] 换源导致信号历史重算、准确率口径变化** → 缓解：D3 强制新旧对照并标注「口径变更非算法改进」，旧口径基线可回溯；按 `AGENTS.md` #5/#8/#9 留痕，不影响算法本体。
- **[风险] 统一限速降低取数吞吐，临近收盘高峰更慢** → 缓解：批次参数沿用现有经验值（板块 6/批、批间 20s、单请求 1s），且只在真正落后时才补齐，盘中和正常覆盖日不触发大批量拉取。
- **[风险] 指数补偿重试在收盘高峰反而加重限流** → 缓解：补偿重试只针对「单只失败」这只，且走统一限速队列，不并发；限流识别即止损，不会叠加。
- **[风险] 多机 pull 覆盖本机 refresh-state.json 导致重复补齐** → 缓解：D8 排除该文件入库或文档约定，覆盖只影响当日尝试计数、不破坏数据。
- **[风险] 健康检查新增字段与现有前端/监控耦合** → 缓解：新增字段为只读扩展，不改现有字段含义；`data_state`/`stale` 语义保持。

## Migration Plan

1. **先测后换（D2）**：跑 `test_source_stability.py`，产出 `data/research/source-eval/` 报告；达标才进入 D3，否则仅落地 D1/D4–D8 并归档。
2. **换源（D3，仅达标时）**：基线打包 → 全量重拉 → 重算三份产物 → 升版 → 出对照报告；结构校验失败保留上一版产物。
3. **调度与采集改造（D1/D4/D5/D6）**：新增 `rate_limiter.py`、改 `fetch_market.py`/`fetch_etf.py`/`bootstrap.py`/`build_dashboard.py`/`snapshot.py`/`scheduler.py`；本地跑 `refresh_daily()` 与 `_backfill_blocking()` 验证三份产物都落盘、各线 `as_of` 可见。
4. **入口与文档（D7/D8）**：改 `重建信号数据.bat`、加 `快速同步拉取代码.bat` 自检、修 README。
5. **多机发布**：`git add -A` 全量提交 → 其他电脑 `git pull`（或跑同步脚本自检后 pull）→ 重启服务 → 比对 `/api/health` 的 `scheduler_version` 与数据源版本。

**回滚**：采集/调度改造为纯源码改动，回滚即 `git revert` 对应提交并重启；换源若对照不达标，旧源基线在 `data/research/source-baseline/` 可一键回指（恢复 manifest 与原始 CSV、重算）；产物设计上保证「删除对照报告后重建生产产物与删除前逐条一致」，回滚不依赖报告文件。

## Open Questions

- 指数日线（同花顺）在 D2 观测中若稳定性明显差于候选源，是否纳入后续换源？——属独立决策，待 D2 实测后再单独确认，不阻塞本变更。
- 统一限速的「批大小/批间间隔」最终值是否要随 D2 实测微调？——可在实施阶段基于 D2 的延迟/限流数据再校准，不影响规格与任务拆解。
