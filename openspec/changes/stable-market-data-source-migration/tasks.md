# Tasks

## 1. 候选源稳定性测试与选型门槛（先于一切换源动作）

- [ ] 1.1 新建 `pipeline/test_source_stability.py` 与产出目录 `data/research/source-eval/`，并写死一套冻结的测试门槛 JSON（成功率下限、限流率上限、延迟上限、日线定稿时刻要求、口径可比性要求），验证：脚本启动时打印冻结门槛且测试中不得改动 `market-data-source-evaluation` 的「切换门槛不切换结论」场景
- [ ] 1.2 在候选清单中同时登记两类免费源（同发行方其他接口/通道 + 其他发行方），排除需凭据的源并记录原因，验证：`source-eval/candidates.json` 含来源标识、接口说明、复权口径、是否需要凭据，且需凭据源未进入生产候选（「候选源范围与登记」场景）
- [ ] 1.3 实现逐线重复取数并采集指标（总请求/成功/失败、延迟分位、当日日线首次可获取时刻、限流恢复时长），验证：报告每条比率同处给出样本量与统计区间、限流触发记录触发与恢复时刻、定稿时刻记录具体时刻而非「当天曾取到」（「稳定性测试口径」场景）
- [ ] 1.4 实现口径可比性检查：板块须与固化 31 个单层行业一一对应、ETF 须与核心 6 只逐一对应，缺项判不可比且禁止按名称近似匹配，验证：缺项时报告列出缺失项并判不可比（「口径可比性门槛」场景）
- [ ] 1.5 生成独立测试报告并落盘 `data/research/source-eval/report.md`，验证：删除该报告与缓存后执行 `build_dashboard.main()` 重建的三份产物与删除前逐条一致（「报告不进生产链路」场景）；未达门槛时报告明确标注「未达标」与逐项实测值、生产主源保持不变

## 2. 跨数据线统一限速模块（D1）

- [ ] 2.1 新建 `pipeline/rate_limiter.py`，实现按发行方（`eastmoney`/`ths`）的串行令牌队列与全局冷却，验证：`acquire(issuer)` 在冷却期返回「跳过」而非重试，`snapshot()` 返回各发行方冷却剩余秒数与跳过数据线
- [ ] 2.2 改造 `pipeline/fetch_market.py` 与 `pipeline/fetch_etf.py`，移除私有 `_em_rate_limited_until` 全局、改用统一限速器，且 ETF 清单校验与日线请求同样受东财冷却约束，验证：板块冷却期内的日志中无任何 ETF 行请求发出（「冷却覆盖 ETF 清单校验」场景）
- [ ] 2.3 将分批参数（批大小/批间间隔/单请求间隔）收敛为统一可配置常量，移除 `call_with_retry` 的 4 次重试叠加，改为限流识别即止损失败，验证：模拟限流时首请求失败即中止本轮、不对该批标的重试（「限流识别即止损」「板块与 ETF 不同时发请求」场景）
- [ ] 2.4 为限速器补 `test_rate_limiter.py` 单元测试（冷却进入/退出、跳过返回、跨发行方隔离），验证：`pytest pipeline/test_rate_limiter.py` 全绿

## 3. 换源实施与全量重拉（D3，仅在 1.5 达门槛后执行）

- [ ] 3.1 切换前把旧源 `data/raw/sectors-em-industry/`、`data/raw/etf-daily/`、两份 manifest、当前三份产物打包到 `data/research/source-baseline/<YYYY-MM-DD>/`，验证：基线目录存在且旧源数据未被覆盖或删除（「旧产物可回溯」场景）
- [ ] 3.2 从 `2020-10-30` 起全量重拉板块与 ETF 日线到新源接口，复用 `ensure_raw_data` 全量分支，验证：每个板块/ETF 的 CSV 全部来自同一主源且覆盖历史起点、无旧源/新源混合序列（「全量重拉不拼接」场景）
- [ ] 3.3 调 `build_dashboard.main()` 重算三份产物，更新 `pipeline/schemas.py` 的数据源标识与算法版本号并随产物输出，验证：结构校验失败时保留上一版产物并输出原因（「校验失败保留旧版」场景）
- [ ] 3.4 输出新旧对照报告（逐板块/逐 ETF 状态与放量事件、换源前后信号准确率逐格对照、标注「口径变更非算法改进」），验证：对照删除后重建的生产产物与删除前逐条一致、任一比率带 Wilson 95% 与统计区间（「换源事件对照与旧结论保留」「差异不表述为改进」场景）

## 4. ETF 数据线接入新鲜度与自动重建（D4）

- [ ] 4.1 在 `bootstrap.latest_local_dates()` / `data_gap()` 增加 `etf` 线（6 只 ETF 尾日最小齐备日），验证：`data_gap()` 返回的 `stale` 在仅 ETF 落后时含 `etf` 且不影响指数/板块/机构三条线的「已覆盖」判定（「不改变新鲜度判定」场景）
- [ ] 4.2 `refresh_daily()` 补调 `update_etf()` 并将 ETF 纳入 `refreshed` 字典，验证：运行 `bootstrap.refresh_daily()` 后 ETF 日线增量被处理、ETF 失败只告警不中断其余线（「单只失败不影响其余」场景）
- [ ] 4.3 `server/scheduler.py` 的 `_backfill_blocking()` 与 `_tick_close()` 在 `changed` 含 `indices`/`sectors`/`etf` 任一变化时额外落盘 `etf-volume-events.json`，验证：本地仅补到 ETF 新交易日时事件产物 `as_of` 随之前进、不晚于最慢标的（「事件产物随收盘重算自动重建」「不晚于最慢标的」场景）
- [ ] 4.4 补 `test_refresh_and_rebuild.py` 覆盖三份产物同时落盘与 ETF 落后触发补齐，验证：`pytest pipeline/test_refresh_and_rebuild.py` 全绿

## 5. as_of 口径统一、单只指数补偿重试与短板可见（D5）

- [ ] 5.1 `build_indices()` 保持 `as_of = min(ends)` 的同时输出 `index_tails`（7 个指数各自尾日），验证：产物含 `index_tails` 且聚合 `as_of` 与声明规则一致（「短板标的可见」场景）
- [ ] 5.2 `update_indices()` 为单只失败增加独立补偿重试（退避 2–3 次），仍失败标记该指数落后并 `continue`，验证：某指数 502 时其余 6 个照常更新落盘、失败指数保留旧尾日不插值（「单只 502 被补偿重试」「失败不伪造」场景）
- [ ] 5.3 `snapshot.py` 的 `build_live_snapshot` 改用与 `build_close_snapshot` 相同的 `as_of` 导出规则，验证：同一批原始日线在收盘/盘中模式 `as_of` 一致、不差一个交易日（「模式切换不产生日期跳变」场景）
- [ ] 5.4 产物与 `/api/health` 同时给出 `as_of`、各指数尾日与落后标的代码，验证：6 尾日 T、1 尾日 T-1 时响应显式标出落后标的（「短板标的随日期一起返回」场景）

## 6. 定时任务重校准与健康检查字段（D6）

- [ ] 6.1 将 `server/scheduler.py` 的 `CLOSE_HOUR_MINUTE` / `EVENING_SLOTS` 改为按新源实测定稿时刻校准并声明，保留「数据未落后时不发请求、积压时刻只执行最靠后一轮」逻辑，验证：到达补齐时刻而全数据线已覆盖时不发起远端请求（「数据未落后时补齐时刻不发请求」场景）
- [ ] 6.2 `MarketScheduler.status()` 增加在用主源标识与版本、`SCHEDULER_VERSION` 常量、限速器冷却快照、各数据线本地 `as_of`/最近已完成交易日/落后交易日数，验证：`/api/health` 响应含上述字段且明确区分「实时快照可用」与「日线已跟上」（「多机版本可比对」「各数据线新鲜度分别可见」「冷却状态可见」场景）
- [ ] 6.3 为 scheduler 健康检查与补齐新增 `server/test_server.py` 用例，验证：`pytest server/test_server.py` 全绿

## 7. 手动重建入口可观测性（D7）

- [ ] 7.1 `重建信号数据.bat` 选项 1（rebuild only）完成后输出三份产物各自的 `as_of` 与相对最近已完成交易日的落后交易日数，并提示「不联网、不会改变数据所属交易日」，验证：原始数据仍落后时连续多次执行，每次输出 `as_of` 与原始尾日一致且不因生成时间更新而显示为最新（「仅重建不联网」「不产生日期前进假象」场景）

## 8. 多机更新与文档修正（D8）

- [ ] 8.1 `快速同步拉取代码.bat` 增加前置自检：`git status --porcelain` 非空时中止并提示提交或 stash，验证：在有未提交改动（如 `data/etf-volume-events.json`）时脚本中止并给出明确提示，不触发 `--ff-only` 失败
- [ ] 8.2 将 `data/raw/refresh-state.json` 加入 `.gitignore`（或文档约定拉取后覆盖回本地副本），消除多机 pull 互相覆盖本机台账，验证：`.gitignore` 生效且该台账不再出现在 `git ls-files`
- [ ] 8.3 修正 `README.md` 中「data/raw/ 不入库」与实际全量入库的矛盾，新增「多机更新」章节（git pull + 重启 + 比对 `/api/health` 的 `scheduler_version` 与数据源版本），验证：README 描述与 `git ls-files data/raw | head` 实际口径一致

## 9. 集成验证（跨组联调）

- [ ] 9.1 本地以落后状态运行 `refresh_daily()` 与一次晚间补齐流程，验证：三份产物均落盘、各线 `as_of` 在 `/api/health` 可见、限流冷却期日志不报「数据已覆盖」，且「删除所有对照报告与缓存后重建三份产物与删除前逐条一致」（各 spec 的「对照不进生产链路」场景）
- [ ] 9.2 在另一台机器按 README「多机更新」步骤 pull + 重启，验证：两端 `/api/health` 的 `scheduler_version` 与数据源版本一致、`task_enabled` 重启后恢复全开
