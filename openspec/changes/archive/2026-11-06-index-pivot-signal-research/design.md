# Design

## Context

动机见 proposal.md（Why）。现状约束与可复用资产：

- 生产信号由 `pipeline/swing_signal_engine.py`（`index-swing6-v2.0`）+ `pipeline/swing_indicators.py` 六因子生成，评价口径是"事后均线反转"（`pipeline/confirmed_index_signals.py` 的 `evaluate_signals`），从未度量与真实价格拐点的距离。
- 生产指数共 7 个（`fetch_market.INDEXES`：上证指数、深证成指、创业板指、科创50、沪深300、中证500、中证1000），日线存于 `data/raw/indices-ths/`（同花顺源，前复权口径固定）。
- 既有区间划分：研究段 `RESEARCH_START=2020-10-30` ~ `RESEARCH_END=2024-12-31`，之后为探索验证段（`research_algorithms.py`）。本调研沿用：**训练段 = 研究段，验证段 = 2025-01-01 起**，embargo 取评价最长前瞻窗口（5 个交易日 + 拐点确认滞后）。
- 外围/情绪辅助数据已有：`data/raw/external/`（两融余额与换手、QVIX 300/50/500、ERP、涨跌家数比、涨停比例、破净率），由 `pipeline/external_data.py` 采集，带 manifest；宽度来自东财行业板块日线（`sectors-em-industry`），ETF 份额/成交额在 `data/raw/etf-*`。
- 研究脚本惯例：纯计算模块 + `run_*.py` 入口，产物落 `data/research/`，带 `input_hash` 与冻结参数（参考 `run_swing_comparison.py`、`factor_survey.py` 的 PREREGISTERED 模式）。

## Goals / Non-Goals

**Goals:**
- 一套可复核的"真实拐点基准集"构建与冻结流程
- ≥3 种价格结构候选拐点算法 + 辅助因子加分框架的离线实现
- 拐点口径三指标（捕捉率/滞后分布/点位偏差）+ 主达标口径的统一评价模块
- 新旧算法同基准对照报告与三态结论，交付给用户决策

**Non-Goals:**
- 不改 `confirmed_index_signals.py` / `swing_signal_engine.py` / `build_dashboard.py` 的任何生产路径，不改 `index-signals.json` 与前端
- 不做实盘仓位/收益回测（只评价拐点定位准确性）
- 不在本变更内决定最终采用哪种算法——结论三态化，由用户拍板后另行发起更换变更

## Decisions

### D1 拐点基准集：ZigZag 事后标注制
- **选择**：用经典 ZigZag 规则在**全历史逐日因果滚动**标注拐点候选，仅当反转在事后达到"幅度阈值 × 反向持续确认"时才把极值日写入基准集；幅度阈值网格 {3%, 5%, 7%}、最小间隔网格 {8, 13, 21} 交易日，在训练段择中选取一组冻结（目标：单指数年均拐点数落在 4–8 个，与"波段"量级匹配）。
- **备选**：人工标注（不可复现、样本少）；分形笔段裸用（噪声大）。
- **理由**：ZigZag 阈值语义与现有引擎的 `min_swing_amplitude=3%`/`min_swing_days=8` 可对齐可比；基准集是"事后事实"，允许使用未来数据标注（只用于评价，绝不进入信号生成）。
- **实现**：新模块 `pipeline/pivot_benchmark.py`，输出 `data/research/index-pivot-benchmark.json`（逐指数：date/type/price/序号 + 冻结参数 + input_hash）。

### D2 候选拐点算法：三种价格结构主干
均为纯因果实现（T 日信号只用 T 日及之前数据），集中在 `pipeline/pivot_algorithms.py`：
1. **pivot-zigzag**：滚动 ZigZag 极值确认破位——反转幅度达标即确认前一极值为拐点（确认滞后=ZigZag 自然滞后）；
2. **pivot-donchian**：滚动 N 日高/低点突破 + 突破后 M 日不创新极值才确认（N∈{20,55}，M∈{3,5}）；
3. **pivot-fractal**：分形（左右各 K 日高低点）+ 事后确认版，等价于通达信"顶分型/底分型"扩展。
- 每种算法输出"信号确认日"作为信号日期，与现行算法"确认日语义"对齐（spec 要求现行算法同语义对照）。
- 参数网格只在训练段选取（每算法保留主指标 Top-1），验证段仅报告一次。

### D3 三层确认机制：候选不叠加以保持语义纯粹
- **选择**：价格结构候选本身已含"事后确认"（破位/不创新极值），不再叠加现行三层确认；三层确认版作为第 4 个变体 `pivot-zigzag+confirm3` 参与对照，用数据回答"叠加是否更准"。
- **理由**：符合用户"结构为主"方向；是否保留确认层交给对照结果，而不是先验拍板。

### D4 辅助因子：只加分、不否决、当日可得
- 池：技术面（RSI/MACD 背离，复用 `swing_indicators`）、情绪/波动（QVIX）、资金面（两融余额 20 日变化、ETF 份额变化率）、宽度（breadth20 分位与 delta5）、外围（隔夜标普500——仅取其 T-1 美股收盘对应 A 股 T 日，即已发布数据）。
- 每条辅助因子输出 0–1 得分与权重（网格 {0.05, 0.10, 0.15} 单因子叠加），只影响信号分级（A/B/C）与"辅助增益"对照列，不改变信号集合。
- 数据可得性按 `signal-factor-discovery` 的 P0/P1/P2 预检并在 manifest 登记；外围数据如需新增采集接口，走 `external_data.py` 现有注册模式，且必须满足"5 年历史 + 当日可得"。

### D5 评价模块与达标口径
- 新模块 `pipeline/pivot_eval.py`：信号集 × 基准集 → 逐指数逐段输出（a）捕捉率（拐点 ±M 日内有信号，M 与幅度阈值一起在训练段冻结，初始候选 M∈{3,5,8}）；（b）滞后分布（中位数、P90）；（c）点位偏差（|信号价−拐点价|/拐点价分布）；（d）主达标率=确认后 ≤5 交易日落入拐点 ±N 日（N 训练段冻结）。
- Wilson 95% 区间复用 `research_algorithms.wilson_interval`；样本 <30 标注小样本。
- 未来函数自检：逐算法"截断重跑"测试（把 T 之后数据删除重算，T 之前信号必须逐条一致），作为单测固化。

### D6 对照入口与产物
- `pipeline/run_pivot_comparison.py`：一键跑基准集 → 全部候选（含变体与辅助增益版）→ 现行 `index-swing6-v2.0` → 汇总报告；产物 `data/research/index-pivot-comparison.json` + `.md`（报告含全候选、淘汰原因、三态结论）。
- 生产保护：入口执行前后对 `data/index-signals.json`、`data/sector-trends.json`、`data/raw/indices-ths/*` 做 sha256+mtime 指纹比对，不一致即报错退出（复用 `verify_etf_volume_revision.py` 的指纹模式）。

## Risks / Trade-offs

- [基准阈值本身决定"什么是拐点"，阈值选偏会整体扭曲对照] → 阈值在训练段用网格 + 年均拐点数约束选取并冻结；报告附 3%/5%/7% 三档敏感性对照，供用户检视结论稳健性。
- [ZigZag 类算法"确认后拐点"存在天然滞后，可能与现行算法滞后相当，调研白做] → 这正是调研要回答的问题；报告以滞后分布为主呈现，即使结论是"保留现行"也有决策价值。
- [验证段（2025 至今）样本可能 <30 拐点了，统计力不足] → 逐指数分别报告并汇总；不足时结论强制降级为"观察"，禁止出"更换建议"。
- [外围数据（美股等）akshare 接口历史长度/发布时点不确定] → 按 P1/P2 机制处理，外围因子缺失不阻塞主干调研，manifest 记录缺失率。
- [研究脚本误写生产数据] → D6 指纹防护 + 单测断言产物路径全部位于 `data/research/`。

## Migration Plan

纯新增研究模块与产物，无生产迁移；回滚 = 删除 `data/research/index-pivot-*` 产物与新模块文件。若调研结论为"更换建议"且用户采纳，另起独立变更修改 `index-signal-engine`（本变更不含）。

## Open Questions

- 科创50 等历史不足 5 年的指数，其基准段是否允许短于 5 年（默认：允许，逐指数标注各自可用区间并在报告中提示样本量差异）。
