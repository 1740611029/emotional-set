# SignalHub · 股市顶部/底部信号可视化

基于**确认型信号**（候选 → 1-3 个交易日 → 价格/动量/量能三层确认）的指数信号站点。
前端为 React + Vite，后端为 Python + FastAPI，信号计算复用经过回归验证的确认型多因子算法。

## 快速开始

```bash
# 1. 安装依赖
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt

# 2. 启动（自动构建前端、按需拉取原始行情）
python start.py
```

打开 <http://127.0.0.1:8000>。也可用 `启动.bat`（Windows）或 `start.sh`（macOS/Linux）。
重复双击不再报端口冲突：启动会先接管 8000，上一次的实例若没关，会先结束它再启动。

环境变量：`HOST`（默认 `127.0.0.1`）、`PORT`（默认 `8000`）、`REFRESH_SECONDS`（默认 `30`）。

## 什么时候会重建信号

**每次启动都会先看数据有没有更新。** 本地日线落后于最近已完成交易日时，服务会**先补齐、补齐之后才出首屏数据**；
页面消费的三条线（指数 / 机构 / ETF）阻塞补齐，板块日线无页面消费、全量补齐要 10-30 分钟，交给后台线程。
补齐失败（无网络、或数据源当天尚未发布）不阻断启动，会用现有数据照常启动，**下一轮缺口检查会再试**。

| 触发 | 时机 | 是否拉行情 | 是否写盘 |
| --- | --- | --- | --- |
| 启动 | 每次运行 | 落后于最近交易日时补齐（指数/机构/ETF 阻塞，板块后台） | 是 |
| 盘中与盘后 | 每 10 分钟检查一次缺口 | 仅落后时增量拉取；已覆盖时零请求 | 是 |
| 收盘任务 | 交易日 17:00 起 | 是（增量拉日线 + 涨停池展示产物） | 是 |
| 龙虎榜日更 | 交易日 20:00 / 22:00 | 是（拉当日榜单） | 是 |
| 手动 | 随时 | 可选（见下） | 是 |

> 收盘/补齐只有在**真的覆盖到最近交易日**时才把当天记为完成。源尚未发布不算完成，
> 按 10 分钟间隔继续重试（盘中同样重试），不会因为「17:00 试过一次」就当天放弃。
> 补齐状态可在 `GET /api/health` 的 `freshness` / `fresh_check` / `progress` / `backfill` 字段查看。

### 手动重建

```bat
重建信号数据.bat              :: Windows，交互选择：回车=仅重建
重建信号数据.bat fetch       :: 先补落后的日线，再重建
重建信号数据.bat fetch --force :: 忽略“当日已补齐”标记，强制再拉一次
```

```bash
./rebuild.sh                   # macOS / Linux，仅重建
./rebuild.sh fetch             # 先补数据再重建
```

仅重建等价于 `.venv\Scripts\python.exe pipeline\build_dashboard.py`；
先补数据再重建等价于 `.venv\Scripts\python.exe pipeline\refresh_and_rebuild.py`。

- 重建前会打印当前生效的算法（`ACTIVE_ALGORITHM`）与版本号；
- 校验失败时不会写盘，旧数据保持不变；
- **重建后无需重启服务**，调度器检测到文件变化后 30 秒内自动刷新快照；
- 重建是幂等的：数据未变化时产出字节级一致的文件。

只想校验不写盘：`.venv\Scripts\python.exe pipeline\build_dashboard.py --validate`。

### 开发模式

```bash
# 终端 1：数据服务
python -m server

# 终端 2：前端热更新
cd signal-hub-web && npm install && npm run dev   # http://localhost:5173
```

`vite.config.ts` 已把 `/api` 代理到 `http://127.0.0.1:8000`，可用 `API_TARGET` 覆盖。

## 数据与刷新机制

| 场景 | 数据源 | 发布时点 | 刷新 |
| --- | --- | --- | --- |
| 指数日线（历史 + 收盘） | 新浪公开指数日线（2026-11-14 起换源，原同花顺） | 收盘后陆续定稿 | 启动/收盘后补齐 |
| 板块日线（90 个同花顺细分行业，不复权；2026-11-14 起换源，原东财行业板块） | akshare 同花顺行业指数接口 | 收盘后陆续可取（2026-11-14 探测实测覆盖最新已完成交易日） | 启动/收盘后增量（起点含本地尾日） |
| ETF 日线（6 只核心池，不复权） | 新浪 ETF 日线（清单校验走腾讯实时） | 收盘后定稿 | 启动/收盘后增量 |
| 盘中指数快照 | akshare 新浪指数快照（备源：东方财富） | 实时 | 交易时段每 30 秒 |
| 机构多空单（前 20 会员持仓排名） | akshare 中金所持仓排名 | 当日晚间披露 | 启动/收盘后增量更新 |

实测（换源前本地文件时间戳）：东财行业板块收盘后即发布，新浪指数日线晚间定稿。
板块线换源后两线节奏仍可能不同，**同一批重建里板块 `as_of` 允许领先指数 `as_of` 至多 1 个交易日**——
指数 `as_of` 取七个指数尾日的最早值，板块 `as_of` 取全部板块尾日的最晚值，属两种数据源发布节奏差异，不是计算缺陷。
两份产物与界面各自显式标注自身时点（板块页的「数据截至」与滞后提示即为此而设），
领先超过 1 个交易日按发布节奏异常核查后再发布。

板块日线已于 2026-11 由申万行业指数**换源为东财行业板块**，并把「一级 + 二级」两层
改为**单层 31 个行业**（换源前的调研结论「不换源」已被这次改动取代）。换源前后四态一致率：
研究段 **70.59%**（样本 30,140 行业日，2020-10-30～2024-12-31）、验证段 **75.46%**
（样本 13,702 行业日，2025-01-02～2026-11-03），其中 bull↔bear 方向相反 5.20%/4.25%，
详见 `data/research/sector-source-migration-sw-vs-em.md`；指数侧横截面宽度同步换源，
顶底信号由 73 条变为 81 条（日期一致 45），对照见 `data/research/breadth-source-migration-sw-vs-em.md`。

板块日线又于 2026-11-14 由东财行业板块**换源为同花顺行业指数**：背景是东财 push2 系自 2026-11-12 起
IP 限流多日无法补齐，且申万一级的 9 条免费日线通道探测全部不可用。粒度由 31 个一级行业变为
**90 个细分行业**，四态判据、有向强度公式与滞后规则逐行不变，算法版本升为
`sector-donchian20-ma60-v3.0-ths-source`。探测与可选路径见
`data/research/source-eval/probe-sectors-conclusion-20261114.md`，换源前东财 v2.0 产物备份在
`data/research/source-eval/manifests-before-migration-20261114/sector-trends.json.before-migration`
（as_of 2026-11-11）。指数侧横截面宽度同批次改用同花顺 90 行业；本次为粒度变更，无申万→东财
那类逐日四态一致率对照，口径以版本升位声明与算法决策日志（`openspec/sector-trend-algorithm-log.md`）为准。

**盘中态与收盘态严格隔离**：

- 盘中以实时行情合成「当日未完成 K 线」，复用同一套确认算法，结果标记 `provisional`，
  只存在于内存与 API 响应，**不写入历史、不计入准确率分母**；
- 收盘后由真实日线重算并原子落盘，作为唯一正式信号；
- 盘中信号会随价格波动重绘，界面统一标注「盘中临时」。

## 目录结构

```
pipeline/            信号流水线（扁平 import，保持与原研究项目一致）
  fetch_market.py      日线抓取（新浪指数 / 同花顺行业指数）
  trading_calendar.py  交易日历与“最近已完成交易日”判定
  research_algorithms.py, multifactor_research.py   指标与因子
  confirmed_index_signals.py, confirmed_sector_trends.py  确认型信号
  candidate_state.py   候选与三层确认状态（只读旁路）
  institution_positions.py  机构多空单（中金所前 20 会员持仓排名，事后统计）
  build_dashboard.py   组装前端 JSON
  io_utils.py, schemas.py  校验与原子写入
  bootstrap.py         原始数据补齐与增量刷新（含新鲜度判定）
  refresh_and_rebuild.py 先补落后的日线再重建 JSON（供「重建信号数据.bat fetch」）
  sector_source_survey.py 板块数据源换源影响调研（只出报告，不改口径）
  sector_source_migration.py, breadth_source_migration.py  换源前后四态/宽度/信号对照（只写 data/research/）
  compare_snapshot.py  信号事实回归比对
server/              数据服务（快照、调度、API）
data/                落盘数据（raw/ 全量随仓库分发，克隆即有历史；
                     仅 data/raw/refresh-state.json 本机台账不入库）
signal-hub-web/      前端（React + Vite + Tailwind）
```

## 接口

| 路径 | 说明 |
| --- | --- |
| `GET /api/health` | 存活、最近刷新时间、模式、数据源、算法版本 |
| `GET /api/institutions/positions` | 机构多空单：`institution`（`all`/`guotai`/`citic`/`others`）、`variety`（`all`/`IF`/`IC`/`IH`/`IM`）、`from`/`to`、`limit`（默认 60） |
| `GET /api/etf/volume-series` | ETF 放量成交额序列（只读展示层） |
| `GET /api/etf/share-flow` | 宽基 ETF 申购净流入（只读展示层） |
| `GET /api/blogger/posts` | 【全能的野人】发文列表 |
| `GET /api/lhb/overview` 等 | 龙虎榜知名游资总览/详情 |
| `GET /api/limit-cycle` | 连板周期只读数据 |

> 2026-11-20 下线「指数总览」与「板块趋势」两页，及其专用的
> `/api/snapshot`、`/api/indices/{code}/history`、`/api/sectors/{code}/history`、
> `/api/sectors/{code}/stocks` 接口。根路径 `/` 现重定向至发文页。
> 同批移除三项已无消费页面的定时任务：**刷新快照 / 盘中实时快照 / 晚间补齐**；
> **收盘任务**改为只刷新机构、ETF、连板等仍在服役页面的数据，不再自动重算指数/板块信号产物
> （如需重建走「重建信号数据.bat」手动触发）。龙虎榜日更、发文提醒保留。
> 2026-11-24（sched-v5.0）：改为**启动即补齐**落后数据、**盘中每 10 分钟复查缺口**；
> 收盘任务按「是否真的覆盖最近交易日」判完成（源未发布继续重试，不再当天放弃）；
> 板块日线无页面消费，由启动后的后台线程补齐。
> 下方「板块数据计算口径」为数据管线说明，非页面文档。

`data_state` 取值：`ready` / `preparing`（首次拉取数据中，返回 202）/ `degraded`（数据源异常，`stale=true`）。

## 测试

```bash
python -m pytest pipeline server -q
```

- `pipeline/`：信号算法、候选状态、校验与原子写入（含从原项目迁移的回归用例）
- `server/`：快照契约、盘中合成、实时源归一化、接口状态

当前回归（2026-11-14）：`python -m pytest pipeline server -q` → **542 passed / 5 skipped / 0 failed**
（125 subtests passed）。此前 535 passed / 7 failed 的 7 个失败全部位于 `server/test_precision_api.py`
（`body["precision"]` 为 None），属既有失败：`build_dashboard.py` 自 commit `27edd53`（底部信号改
bigwave 算法）起不再输出旧 swing 口径的 precision 矩阵块，服务端/前端/schema 均已按「可选块、
缺失放行」处理，仅测试契约未同步。本次按修法 B 对齐：只改 `server/test_precision_api.py`，
生产代码/接口/前端/数据产物零改动；precision 缺失视为合法空态直接通过，若未来恢复输出则
严格校验矩阵结构/口径。

回归比对（信号事实必须逐条一致）：

```bash
python pipeline/compare_snapshot.py --baseline <旧 index-signals.json> --current data/index-signals.json
```

## 板块数据计算口径（原「板块趋势」页，页面已于 2026-11-20 下线；数据仍供快照与信号使用）

- **清单与数据源**：90 个同花顺细分行业指数（881xxx），清单由 akshare
  `stock_board_industry_name_ths` 动态返回并固化进 `data/raw/sectors-ths-industry-manifest.json`
  （少于 60 个视为接口异常拒绝采集，`fetch_market.THS_MIN_UNIVERSE`）；**单层、无父子关系**，
  不复权日线，缓存 `data/raw/sectors-ths-industry/{881xxx}.csv`；盘中与收盘同源
  （`server/paths.SECTOR_THS_RAW == fetch_market.THS_SECTOR_RAW`）。
  前身：申万两层（v1.0/v1.1）→ 东财 31 行业（v2.0），均已留痕（见本节末尾）。
- **四态判据**（算法版本 `sector-donchian20-ma60-v3.0-ths-source`，判定规则本体与 `v1.0` 一致，
  版本号升位只标识数据源与清单变更）：
  收盘价相对唐奇安 20 日通道中轨、相对 60 日均线两条判据组合成
  `bull`（多头，两者皆上）、`warn`（退潮，破中轨仍在 MA60 上）、
  `bear`（空头，两者皆下）、`rise`（反转，上穿中轨但仍在 MA60 下）。有效样本要求 60 个交易日。
- **每个板块用自身日线独立判定**，记录中无 `level`/`parent`/`subsectors`，也没有“与父级不一致”这类概念。
- **有向强度 -50 ~ +50**：`(区间位置分 + MA60 偏离分) / 2 - 50`，其中区间位置分为收盘价在近 20 日高低区间中的百分位，
  MA60 偏离分为 `50 + 50·tanh(偏离/10%)`。0 为中性，正值偏强、负值偏弱，
  四种状态使用同一公式，不按多空取反；通道宽度为零时固定为 0 并标记 `data_anomaly`。
  例如两项均值 80 对应强度 30，两项均值 20 对应强度 -30。
  改动前 0–100 且空头/退潮取反的旧产物已备份为 `data/sector-trends.pre-signed-strength.json`。
- **滞后与展示降级**（自换源前沿用，未收紧未放宽）：尾日早于产物 `as_of` 的板块标记 `data_lag`，
  仍展示其状态；滞后超过 30 个自然日则清空 `state`/`strength`/`strength_parts`/`strength_basis`/`days`，
  宁可显示无状态也不用陈旧结论示人。该规则只作用于最新条目，不进入任何历史序列与信号计算。
- **算法决策日志（改算法前必读）**：`openspec/sector-trend-algorithm-log.md` 记录了本页已测试不通过、
  通过未采用与当前采用的全部算法口径；任何算法修改前必须先完整阅读该文档，测试结束后必须回写其变更登记表
  （AGENTS 第 14 条）。
- **准确率披露**：产物 `evaluation` 按研究段 / 验证段 × 10 / 20 日、**再按四态分组**给出方向胜率，
  每格同时给已判定样本、成功数、未决数与 Wilson 95% 区间。**四态样本之和恒等于拆分前的聚合样本**，
  需要总体口径时直接相加即可（研究段 10 日 13,639 / 20 日 13,533；验证段 10 日 6,274 / 20 日 6,186）。
  方向按状态期望：`bull` / `rise` 期望上涨，`warn` / `bear` 期望下跌；未决事件不计入分母。
  格内格式为「方向胜率（已判定样本，Wilson 95% 区间）」，数据照录生产 `data/sector-trends.json`
  （as_of 2026-11-13）：

  研究段（`research`，2020-10-30～2024-12-31）

  | 状态 | 10 日 | 20 日 |
  | --- | --- | --- |
  | `bull` 多头 | 52.38%（3,795，50.79%–53.97%） | 50.39%（3,765，48.79%–51.98%） |
  | `warn` 退潮 | 54.10%（3,100，52.34%–55.84%） | 54.81%（3,027，53.03%–56.57%） |
  | `bear` 空头 | 53.72%（3,604，52.09%–55.34%） | 51.96%（3,601，50.32%–53.59%） |
  | `rise` 反转 | 52.61%（3,140，50.86%–54.35%） | 53.47%（3,140，51.72%–55.21%） |

  验证段（`exploratory_validation`，2025-01-02 起）

  | 状态 | 10 日 | 20 日 |
  | --- | --- | --- |
  | `bull` 多头 | 57.38%（1,924，55.16%–59.57%） | 58.12%（1,908，55.90%–60.32%） |
  | `warn` 退潮 | 49.22%（1,605，46.78%–51.67%） | 51.04%（1,591，48.58%–53.49%） |
  | `bear` 空头 | 43.10%（1,492，40.60%–45.62%） | 41.43%（1,453，38.92%–43.98%） |
  | `rise` 反转 | 70.23%（1,253，67.64%–72.70%） | 70.66%（1,234，68.06%–73.14%） |

  产物自带 `research_status=post_hoc_exploratory_not_independent`：**验证段为事后观察、不构成独立验证**；
  说明文字为「板块趋势为描述性分类，方向胜率不代表交易收益；空头状态在验证段胜率低于 50%、
  研究段约 53%，仅供参考」——**仅作描述性分类，不代表交易收益**。

重新生成板块产物：

```bash
python -c "import sys; sys.path.insert(0,'pipeline'); from build_dashboard import build_sectors; from io_utils import protected_write; protected_write('sector-trends.json', build_sectors(), compact_arrays=True)"
```

板块回归比对（`compare_snapshot.py` 不带参数时现场重建并与生产产物逐指数对账，
`--baseline-sectors` 可叠加板块级对账：板块只比 `state`/`strength`/`days`/分组；指向不存在的文件则跳过板块部分）：

```bash
python pipeline/compare_snapshot.py
```

换源留痕（均不再参与比对）：申万两层基线 `data/research/sector-trends-baseline.json`
（`v1.0`，as_of 2026-10-30，可比性由 `pipeline/sector_source_migration.py` 对照报告接管）；
东财 v2.0 产物备份 `data/research/source-eval/manifests-before-migration-20261114/sector-trends.json.before-migration`
（`v2.0-em-source`，as_of 2026-11-11）。

## 信号算法（当前 `index-bigwave-bottom-v1.0`）

生产信号由 `pipeline/confirmed_index_signals.py` 生成，`ACTIVE_ALGORITHM` 三选一：
`bigwave`（现行）/ `swing`（回滚）/ `legacy`（回滚）。

- **现行 `bigwave`（大波段底部，仅底部信号）**：自适应幅度 ZigZag 极值（幅度线 = k × 训练段年化波动，
  按波动分 3 组冻结参数）→ 1~3 日三层确认（价格 → 动量 → 量能）→ 信号日记为确认日；
  顶部按用户 2026-11-09 决定暂时关闭；另输出「超卖 + 宽度冰点」情绪底背景带。
  逐指数人工核对表见 `data/research/bigwave-bottom-checklist.md`。
- **回滚 `swing`（`index-swing6-v2.0`）**：六个正交方向独立打分、等权合成；两道闸门（核心三项
  至少两项达到极端 + 趋势"加速恶化"否决底部候选、顶部对称）；`bottom` 与 `top` 波段严格交替，
  同向候选在出现反向信号前一律丢弃，要求最小波段间隔与振幅。
- **回滚 `legacy`（`index-mf4-participation-v1.1-confirmed`）**：四因子确认算法，swing6 之前的生产口径。
- **横截面宽度**：90 个同花顺行业指数站上 MA20/MA60 的占比及 5 日变化（2026-11-14 起与板块线同源）；
  2026-11-03~11-14 为东财 31 行业，更早为申万两层。
- **三层确认与分级**（bigwave / swing 共用）：价格（突破候选日高低点）→ 动量（K 线方向 + RSI 转向，
  顶部额外要求 MA5 掉头）→ 量能（放量或宽度改善 ≥5pp），窗口 1–3 个交易日，超时作废；信号日期记为确认日。
  A 级要求核心三项全部极端且分数不低于本指数信号中位数，其余为 B 级。
- **算法决策日志（改算法前必读）**：`openspec/index-signal-algorithm-log.md`，规则同板块页（AGENTS 第 14 条）。

### 回撤口径

- 每个信号以信号日收盘价为基准，输出 `max_adverse_excursion`（至确认日的极值回撤）、`max_adverse_excursion_20`、`max_drawdown_20` 与 5/10/20 日前瞻收益；
- 汇总层输出平均/最差最大回撤、平均 20 日收益与收益回撤比；
- 回撤**不参与胜率计算**，修改回撤窗口不改变任何胜率数值；
- 所有页面凡展示胜率或收益，必须同屏展示回撤；无样本时显示 `--`，不显示 0。

### 研究口径

- 训练段 `2020-10-30 ~ 2024-12-31` 选参，验证段 `2025-01-01` 起仅作报告，隔离期 20 个交易日；
- swing6 择参规则：底部/顶部 Wilson 下界取小，已判定样本 <20 淘汰；一致性要求 ≥4/7 个指数的
  20 日前瞻收益高于同期全样本均值；冻结参数与淘汰记录见 `data/research/swing-factors-index-swing6-v2.0.json`，
  新旧对照见 `data/research/swing-vs-legacy-index-swing6-v2.0.json`；
- bigwave 择参：训练段按指数年化波动分 3 组冻结（objective = train direction + big-band hit
  under per-year ≤ 2.5），验证段未参与择参；研究源 `pipeline/research_dual_bottom.py`，
  结论 `data/research/dual-bottom-research.json`；
- 研究脚本只写 `data/research/`，不触碰生产 JSON。

## 数据口径与约束

- 复权方式与数据源已固定，变更等于更换一套信号历史，需单独评审（板块线 2026-11 两轮换源均走了
  单独评审与留痕：申万→东财有逐日四态一致率对照；东财→同花顺为粒度变更，以探测结论与版本升位
  声明为准，均见 `openspec/sector-trend-algorithm-log.md`）；2026-11-14 指数线（同花顺→新浪）与
  ETF 线（东财→新浪，清单校验→腾讯）经用户授权以可用性优先换源，信号事实对照见
  `data/research/source-eval/source-migration-20261114.md`（信号日期/类型 100% 一致）；
- 信号只在三层确认全部通过后产生，未确认候选不计入准确率；
- 页面展示准确率时必须同时给出已判定样本数与统计区间，并并列展示回撤；
- 原始日线 `data/raw/` **全量随仓库分发**（git ls-files 可核）：生产实际使用约 23MB
  （中金所 10.9MB + 同花顺行业板块 9.0MB + 指数等），克隆即有历史；另有约 41MB 的旧申万板块缓存
  与约 9.1MB 的东财 31 行业缓存（2026-11-14 板块线换源后转为留痕）不参与任何计算
  （删除后重建结果逐字节一致，已验证）；
  唯一例外是 `data/raw/refresh-state.json`：本机补齐台账（当日尝试次数与完成标记），
  已加入 `.gitignore`，多机 pull 不会互相覆盖；
- `data/*.json` 随仓库分发，作为离线首屏兜底。

### 机构多空单口径

- 数据为中金所每日**前 20 会员**持仓排名（akshare `get_cffex_rank_table`），非全市场持仓；多单/空单均为**单边持仓量**，单位为**手**。
- 会员名按 `MEMBER_ALIASES` **精确匹配**归一（同时剥离 `(代客)`/`(自营)` 后缀）；`中信建投` 与 `中信期货` 是两个主体，不得合并。
- 四品种汇总为 IF/IC/IH/IM 手数直接相加，**未按合约乘数折算**（IF/IH 300 元/点、IC/IM 200 元/点），不可用于跨品种规模比较。
- 同向/对冲为**事后统计**：当日净持仓变化方向与**次日**沪深300 涨跌方向相同为同向、相反为对冲；次日数据未产生时为"待判定"，不计入同向率分母。该字段不参与任何信号计算。
- 重建命令（首次全量约 5–10 分钟，已缓存日期不重复抓取）：

```bash
python pipeline/institution_positions.py            # 增量
python pipeline/institution_positions.py --limit 60  # 仅最近 60 个交易日（调试）
python pipeline/institution_positions.py --force     # 忽略缓存重抓
```

原始明细缓存于 `data/raw/cffex/{YYYY-MM-DD}.csv`（全量入库，随仓库分发）。

## 多机更新

定时任务逻辑全部写在源码（`server/scheduler.py` 的时刻常量与调度分支），
任务开关是进程内状态（重启后恢复全开），**不存在需要迁移的落盘调度配置**。
其他电脑按以下步骤验收即可：

1. **同步前自检**：确认工作区干净（`git status --porcelain` 应为空）。
   运行时刷新的 `data/*.json` 属常见未提交改动；`快速同步拉取代码.bat` 已内置该自检，
   有未提交改动时先 `git stash` 或提交，否则 `--ff-only` 必失败。
2. **拉取并重启**：`git pull`（或跑 `快速同步拉取代码.bat`），然后重启服务（`启动.bat`）。
   定时任务开关重启后自动恢复全开，无需手动开启。
3. **版本比对**：两端分别请求 `GET /api/health`，比对：
   - `scheduler_version`（调度版本，随调度逻辑变更升版）；
   - `primary_source`（在用主源标识：指数 / 板块 / ETF 三条线的数据源）；
   两者一致即完成同步；不一致说明某台机器未 pull 到最新代码。
4. **数据新鲜度分别核验**：`/api/health` 的 `freshness` 字段逐线给出
   `as_of`、最近已完成交易日与落后交易日数（indices / sectors / institutions / etf 四条线）；
   `live_snapshot_available`（实时快照可用）与 `daily_up_to_date`（日线已跟上）是两个独立标记，
   仅有前者不代表数据新鲜。

`data/raw/refresh-state.json` 是本机补齐台账（当日尝试次数与完成标记），已排除入库；
另一台机器拉取后用自己的台账继续补齐，互不影响。

## 常见问题

**启动时报 `SyntaxError: Non-ASCII character ... but no encoding declared`？**
说明执行脚本的 `python` 是 Python 2（本项目只支持 3.10+）。`启动.bat` 会自动挑选
`.venv` → `py -3` → `python3` → `python`（校验版本），正常不会触发。
若你手动执行 `python start.py`，请改用 `py -3 start.py`，或先激活虚拟环境。

**首次启动很慢？** 需拉取 7 个指数 + 90 个行业板块的日线，逐标的串行请求（板块首次全量约 630 请求、
实测约 25 分钟），首轮耗时主要取决于网络；页面会显示「数据准备中」。
**启动后页面长时间「数据准备中」？** 启动会先把落后的日线补完才出首屏：
可看 `GET /api/health` 的 `stage`/`detail` 确认在拉哪一类；补齐报错也会继续启动，`last_error` 保留原因。

**页面显示「数据陈旧」？** 实时源不可用，服务已回退到最近一次成功数据，恢复后自动切回。

**端口被占用 / 启动报 `WinError 10048`？** 启动会自动结束占用该端口的进程（上一次没关的实例，
或别的程序），结束前打印占用者的进程名、PID 与命令行；系统关键进程不会被结束。
要保留占用者、只做提示：`python start.py --no-kill`（或 `启动.bat --no-kill`）。
仍然异常时可 `启动.bat --restart`（等同默认，先结束再启动）；换端口：`set PORT=8080` 后重新运行。

**只看到收盘数据？** 非交易时段（含周末与节假日）不会请求实时行情，属预期行为。
