# 【指标总览】页（指数顶底信号）算法决策日志

> **强制规则（对任何 AI 代理 / 任何电脑上的任何模型生效）**：
>
> 1. 修改指数信号算法（候选生成、闸门、三层确认、分级、评价口径）之前，**必须先完整阅读本文档**，禁止凭记忆或猜测直接改代码。
> 2. 任何测试、推理、调参、外部指标验证，**结束后必须把结论回写本文档**（通过与否、样本量与区间、留痕文件路径），无论是否被采用。
> 3. 准确率（方向胜率）第一优先级（AGENTS #2）；信号只在三层确认全部通过后产生，未确认候选不计入准确率。
> 4. 同一个候选**不得重复测试**：动手前先查第 4/5/6 节与末尾登记表。回测结果一律落 `data/research/` 并在登记表登记路径。
> 5. 评价口径固定为"后续出现均线反转"（`ma_reversal_masks`）；回撤指标只做并列诊断，不进胜率分母，改回撤窗口不改胜率。

---

## 1. 当前生产算法

**算法版本**：`index-bigwave-bottom-v1.0`（大波段底部引擎，"方案 H"）
**开关位置**：`pipeline/confirmed_index_signals.py::ACTIVE_ALGORITHM = "bigwave"`（三选一：`bigwave` / `swing` / `legacy`）
**核心代码**：`pipeline/bigwave_bottom_engine.py`（纯计算）+ `pipeline/swing_signal_engine.py`（三层确认 `layer_state`、`prepare` 复用）+ `pipeline/swing_indicators.py`（六因子 `factor_table`）
**数据**：7 只指数日线（同花顺源，2026-11-14 起），不复权；`data/raw/indices-ths/`
**重要**：**只发底部信号，顶部按用户 2026-11-09 决定暂时关闭**（`KINDS` 保留 top 但生产不输出）。

### 1.1 判定流程

```
自适应幅度 ZigZag 极值确认（严格因果，极值只在反向运动达幅度阈值且满足最小间隔后才确认）
  → 候选极值 → 1~3 日三层确认（价格 → 动量 → 量能，复用 swing 引擎）
  → 底部信号，信号日 = 三层确认通过日（绝不早于该日）
```

### 1.2 冻结参数（按指数波动分 3 组，防逐指数过拟合）

训练段 `2020-10-30 ~ 2024-12-31` 冻结；幅度线 = k × 该指数训练段年化波动；验证段未参与择参。

| 组 | 指数 | k | min_gap | use_ma60 |
| --- | --- | --- | --- | --- |
| steady | sh000001 上证、sh000300 沪深300 | 0.45 | 30 | True |
| mid | sz399001 深证成指、sh000905 中证500、sh000852 中证1000 | 0.55 | 30 | True |
| active | sz399006 创业板指、sh000688 科创50 | 0.45 | 20 | True |

择参目标（FROZEN_NOTE）：`train direction + big-band hit under per-year<=2.5`。
人工核对清单：`data/research/bigwave-bottom-checklist.md`（方案 H，逐指数信号表：上证 0 / 深成 2 / 创业板 1 / 科创50 4 / 沪深300 3 / 中证500 3 / 中证1000 3）。

### 1.3 附加输出

- 「超卖 + 宽度冰点」情绪底背景带：position ≥ 0.80 且 breadth ≥ 0.55 的连续区间（`ZONE_POSITION=0.80`、`ZONE_BREADTH=0.55`），供前端浅色底纹。
- 回撤口径：每信号输出 `max_adverse_excursion`（至确认日）、`max_adverse_excursion_20`、`max_drawdown_20` 与 5/10/20 日前瞻收益；汇总层含收益回撤比。

## 2. 保留的回滚算法（通过历史验证、曾为生产、现未启用）

| 版本 | 开关值 | 结构 | 状态与背景 |
| --- | --- | --- | --- |
| `index-swing6-v2.0` 六方向波段引擎 | `swing` | 六正交方向（位置/动量背离/量能极值/波动/趋势/横截面宽度）等权打分；两道闸门（核心三项至少两项极端 + 趋势加速恶化否决）；波段严格交替；三层确认；A/B 分级。冻结参数：score_threshold 0.52、core_extreme 0.60、confirm_window 3、min_swing_days 8、min_swing_amplitude 0.03、grade_quantile 0.5、volume_ratio 1.2、breadth_improve 0.05 | 2026-10-31 上线为生产（对照 legacy：信号更多、同区间胜率更好）；后被 bigwave 替换，**保留为回滚开关**。择参与淘汰记录：`data/research/swing-factors-index-swing6-v2.0.json`（择参规则 min(底/顶 Wilson 下界)、已判定<20 淘汰、训练段 ≥4/7 指数 20 日超额）；新旧对照 `data/research/swing-vs-legacy-index-swing6-v2.0.json` |
| `index-mf4-participation-v1.1-confirmed` 四因子确认 | `legacy` | 四因子（participation 系）+ 均线反转评价 | 更早的生产算法，被 swing6 替换后保留开关。核心代码 `pipeline/multifactor_research.py::confirmed_multifactor_signals`；研究链 `multifactor_research.py` / `factor_survey.py` / `multifactor_robustness.py` |

切换方式：改 `ACTIVE_ALGORITHM` 即可，三套代码与字段同仓库共存；**任何切换都要在登记表记录**。

## 3. 版本演变时间线

| 版本 | 日期 | 变更 | 留痕 |
| --- | --- | --- | --- |
| mf4 四因子 | 项目初期 | multifactor 研究链 → legacy 四因子上线 | `data/research/swing-vs-legacy-…json`（legacy 侧基线） |
| `index-swing6-v2.0` | 2026-10-31 | 六方向波段引擎上线为生产，legacy 转回滚 | `openspec/changes/archive/2026-11-01-index-swing-topbottom-signals/`（该变更目录 11-01 归档） |
| `index-precision-pivot-v1.0` | 2026-11-06~09 | 精度改造研究 + 择参（pivot_amplitude 0.05、pivot_min_gap 8、六因子门 core_hits_min=3/direction_min=0.05/min_available_directions=6）→ **决策"观察"，未切换**（见 §4.1） | `openspec/changes/archive/2026-11-06-index-pivot-signal-research/`、`2026-11-09-index-precision-pivot-signals/`；`data/research/precision-decision.json`、`precision-frozen-params.json` 等 precision-* 全系列 |
| 顶部关闭 | 2026-11-09 | 用户决定顶部信号暂时关闭 | bigwave 引擎头注释 |
| `index-bigwave-bottom-v1.0` | 2026-11（precision 之后） | 双底研究 → 大波段底部方案上线为生产（顶部关闭） | `pipeline/research_dual_bottom.py`、`data/research/dual-bottom-research.json`、`bigwave-bottom-checklist.md`、`bigwave-pivot-preflash.md/json` |
| 外部信号调研 | 2026-11-14 | 19 个外部体系/指标增量检验，不改变生产（见 §4.2/§5） | `data/research/external-signal-survey.md/json`、`pipeline/external_data.py` |

## 4. 测试过且不通过（淘汰，勿重复测）

### 4.1 precision-pivot 精度改造（2026-11-09，决策"观察"→未采用）

- 新算法 `index-precision-pivot-v1.0` 训练段 20 日方向正确率 **14.3%**（7 信号，n=7，Wilson 2.6%–51.3%）vs 现行算法 **65.5%**（55 信号，Wilson 52.3%–76.6%）；验证段新算法 2/2=100%（n=2）但与现行 Wilson 区间大幅重叠。
- 稳健性判定：训练段与验证段结论相反 → 不能据此更换。决策原文见 `data/research/precision-decision.json`。
- 注意：该判定依据是"两段表现相反"的稳健性事实，不是重新引入已放宽的样本数门槛。

### 4.2 外部数据型指标增量检验（2026-11-14，预注册门槛不可放宽）

门槛：训练段 2020-11-01~2023-12-31、验证段 2024-01-01~2026-10-29、相关性判重复 |ρ|≥0.6、叠加权重固定三档 0.1/0.15/0.2、验证段有效样本下限 20、P0 最低历史 5 年。

| 指标 | 结论 | 数据 |
| --- | --- | --- |
| 50ETF 期权 QVIX | ❌ 放弃 | 验证段胜率中位 −0.9%（路径 overlay），无增量 |
| 全A 破净率 | ❌ 放弃 | 验证段胜率中位 −2.8%，无增量 |
| 融资买入额/融资余额 | ❌ 放弃 | 验证段胜率中位 −7.1%，无增量 |

### 4.3 外部体系不可复现（无需再测）

| 体系 | 淘汰原因 |
| --- | --- |
| Howard Marks《周期》情绪钟摆 | 无可复现量化定义，无法构造无未来信息的日频序列 |
| Edwards & Magee 形态/趋势线 | 形态标注不可自动化、主观，不满足可复现与 point-in-time |
| De Bondt & Thaler 过度反应 | 3–5 年时间尺度，样本长度不足；信息已被 position 维覆盖 |
| A股情绪周期六阶段（冰点~退潮） | 依赖东财股池接口历史不可回溯；1–5 日尺度与波段顶底不匹配 |
| 新闻/文本情绪（Tetlock 2007、FEARS） | 无 point-in-time 历史语料，回溯构建必引入未来信息 |

## 5. 通过但未采用 / 待下一轮验证

| 候选 | 结论 | 未采用原因 / 下一步 |
| --- | --- | --- |
| 融资余额分位 | ✅ 采纳（唯一通过增量检验） | 验证段胜率中位 +0.3%（|ρ|=0.48），与基线 Wilson 区间重叠 → 仅"值得进入下一轮独立验证"，未进生产；回归时必须用独立验证轮重新报告 |
| Weinstein 30 周均线四阶段 | 观察 | 核心信息已被 trend 维覆盖，需先证增量 |
| Elder 三重滤网 | 观察 | 项目三层确认已实现类似多尺度过滤 |
| O'Neil 追随日（follow-through day） | 观察 | 现有确认机制已含放量条件，差异仅阈值层面 |
| Baker & Wurgler 情绪指数 | 观察 | 构成变量多为月频，A股需自建口径 |
| Zweig 宽度推力 | 观察 | 个股层新高新低接口仅约 2 年；行业层已被 breadth 维覆盖 |
| CNN Fear & Greed | 观察 | 7 分量中 3 个在 A 股无日频对应；结构（等权+分位标准化）可借鉴 |
| 中信建投合成情绪指数 | 观察 | 合成口径不可完全复现 |
| 股债利差 ERP（FED model） | 观察 | 指数市盈率仅月频，跨月填充会制造隐性未来信息 |
| 20 日创新高/新低家数 | 观察 | 历史仅约 2.1 年（不足 5 年门槛），与 breadth 同源 |
| 涨停/跌停家数、连板高度、炸板率 | 观察 | 历史仅约 0.2 年不可回溯；1–5 日短线情绪与波段不同层级 |
| 沪深300/中证500 ETF 期权 QVIX | 观察 | 缺失率 41.8% / 65.2%，超 20% 门槛未进检验 |

## 6. 候选方法池与留痕缺口（重要）

`pipeline/research_algorithms.py::INDEX_METHODS` 保留了 **12 个候选信号方法**：

`RSI14`、`KDJ9`、`WilliamsR14`、`CCI20`、`BOLL20`、`Zscore60`、`MACD12_26_9`、`ROC5 reversal`、`20d extreme`、`MA5_60 pullback`、`RSI+BOLL`、`MACD+RSI`。

- 回测入口：`pipeline/run_algorithm_research.py::run_index_research()`（输出 overall / by_index / by_year 三维汇总到 `data/research/first-round.json`）。
- **留痕缺口（如实声明）**：first-round.json 从未提交 git 且已不在磁盘，**12 种方法的逐个胜率数字不可追溯**。已知事实：项目路线最终走向多因子合成（mf4 → swing6 → bigwave），这 12 个方法保留为研究用途。
- 因此：**把"12 个方法都测过且差"当作未证实假设**；重新选型需重跑并落盘留痕。

## 7. 新算法测试规范（后续推理/测试必须遵守）

1. **调参与验证分离**（AGENTS #7）：训练段 2020-10-30~2024-12-31 选参，验证段 2025-01-01 起仅作报告，隔离期 20 个交易日；择参目标沿用"min(底/顶 Wilson 下界)，已判定 <20 淘汰、训练段 ≥4/7 指数 20 日超额"或更高标准，需事先声明。
2. **对照基准**：新算法与 `ACTIVE_ALGORITHM` 当前值在同区间、同口径对照（信号数、已判定样本、胜率、Wilson 区间、平均回撤、20 日收益），结论写登记表；旧算法不输出回撤字段时只并列胜率与样本量并注明。
3. **稳健性要求**：训练段不得显著差于现行且两段结论方向一致；两段相反一律"不切换"。
4. **禁止未来函数**（AGENTS #1）：ZigZag/极值类必须严格因果（bigwave 的实现是参照样板：极值仅在反向运动确认后才成立，信号日=确认日）。
5. **准确率必须带样本量**（AGENTS #8）：样本数、统计区间、Wilson 95% 区间齐备。
6. **外部指标**：沿用 §4.2 预注册门槛流程（`external_data.py` + `data/research/external-signal-survey.*`），门槛调整必须作为新一轮检验重新报告。
7. **回写本文档**：无论通过/不通过/观察/搁置，登记表加一行。
8. **研究脚本只写 `data/research/`**，不触碰生产 JSON。

## 8. 变更登记表（新测试/推理必须追加）

| 日期 | 候选/变更 | 类型 | 结论 | 样本量与区间 | 留痕 |
| --- | --- | --- | --- | --- | --- |
| 项目初期 | 12 种 INDEX_METHODS 选型 → 多因子路线 | 信号选型 | 走向 mf4 合成（其余数字不可追溯） | — | first-round.json 未入库（缺口） |
| 项目初期 | mf4 四因子（legacy）上线 | 生产算法 | 采用，后被 swing6 替换、保留开关 | 见 swing-vs-legacy | multifactor_research.py |
| 2026-10-31 | swing6-v2.0 上线，legacy 转回滚 | 生产算法 | 采用 | 对照文件全量 | 2026-11-01-index-swing-topbottom-signals |
| 2026-11-09 | precision-pivot v1.0 | 精度改造 | ❌ 不通过（决策"观察"，两段结论相反） | train n=7 vs n=55；validation n=2 | precision-decision.json 等 precision-* 系列 |
| 2026-11-09 | 顶部信号关闭 | 范围决定 | 生效（仅底部） | — | bigwave_bottom_engine.py 头注释 |
| 2026-11（precision 后） | bigwave-bottom v1.0（方案 H）上线 | 生产算法 | 采用 | 逐指数人工核对表 | bigwave-bottom-checklist.md、dual-bottom-research.json |
| 2026-11-14 | 19 个外部体系/指标调研检验 | 外部维度 | 采纳 1（融资余额分位，待独立验证轮）/ 放弃 8 / 观察 10 | 见 §4/§5 | external-signal-survey.md |
| （待填） | | | | | |
