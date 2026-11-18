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

### 5.1 七指数顶底大规模网格研究（2026-11-17，待用户选型）

- 范围：经典书籍/平台、公开大V与机构公开方法归纳后，实际编码 **53 个算法族、115 组参数算法**，每组再跑 4 组三层确认参数；七指数共 **6,440 行候选结果**，另有 28 行生产/回滚基线。缺少 point-in-time 数据的方法单列未测试，不以宣传胜率代替回测。
- 区间：训练 `2020-10-30~2024-11-19`，训练末尾隔离 30 个交易日，验证 `2025-01-01~2026-11-17`；输入哈希 `255175b6957e0b63d30f3d991f58a9ea3f5fba5b5db070b894e6cfcea27e577b`。
- 口径：同时保留固定 60 日单方向评价与实际顶底组合的项目原口径。前者有 13 个“指数×方向”达到训练已判定至少 20 条且覆盖 2 年，上证顶部无正式候选；后者有 6 个指数得到正式组合，上证无顶底同时达标组合。
- 关键结论：两套口径不可混用。例如创业板顶部分形 k2 固定 60 日验证为 72.7%（8/11），实际组合原口径验证为 27.3%（3/11）；各方向完整准确率、Wilson 95% 区间、信号数与组合结果见报告和 JSON。
- 因果与回归：115 组参数算法全部通过完整/截断历史前缀一致性测试；信号与研究回归 `54 passed, 3 skipped + 115 subtests passed`。生产 bigwave、swing6、生产 JSON 与前端均未修改，**本轮仅研究，尚未采用，等待用户逐指数逐方向选型**。
- 留痕：`data/research/top-bottom-optimization-report.md`、`top-bottom-optimization-grid.csv`、`top-bottom-optimization-results.json`；代码 `pipeline/top_bottom_candidate_algorithms.py`、`pipeline/run_top_bottom_optimization.py` 及对应两个测试文件。

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
| 2026-11-17 | 53 算法族、115 参数算法 × 4 组三层确认的七指数顶底网格 | 信号选型研究 | 13 个单方向、6 个实际顶底组合达训练样本门槛；仅研究待用户选型，未改生产 | train 2020-10-30~2024-11-19；validation 2025-01-01~2026-11-17；逐方向 n/Wilson 见报告 | top-bottom-optimization-report.md / grid.csv / results.json |

## 9. 2026-11-17 顶底研究完整结果与跨电脑续训交接

> 本节就是后续在另一台电脑、由其他模型继续研究的交接入口，不另建交接文档。所有路径相对项目根目录。当前仍是**研究完成、等待用户选型**；不得把下文首选误当成已经生效的生产配置。

### 9.1 当前生产与本轮边界

- 当前生产仍为 `index-bigwave-bottom-v1.0`，只输出底部；顶部生产信号仍关闭。
- 本轮未修改 `bigwave_bottom_engine.py`、`confirmed_index_signals.py`、`swing_signal_engine.py`、生产 JSON 或前端。
- 用户要求每个指数、每个顶/底方向都可独立选算法，准确率优先，但不能破坏价格、动量、量能/行业宽度三层确认。
- 验证段本轮已经查看；若据此继续调参，它就不再是独立验证，必须保留新的未来数据或另设未触碰区间作最终检验。

### 9.2 另一台电脑必须复制的研究链

建议复制完整项目，不复制本机 `.venv`，在新电脑重建环境。至少不能缺少：

| 路径 | 用途 |
| --- | --- |
| `AGENTS.md`、本文档 | 强制规则、历史淘汰结论、本轮协议和结果 |
| `pipeline/top_bottom_candidate_algorithms.py` | 53 算法族、115 参数算法、候选与三层确认 |
| `pipeline/run_top_bottom_optimization.py` | 网格回测、双口径评价、实际组合复算、报告生成 |
| `pipeline/test_top_bottom_candidate_algorithms.py`、`pipeline/test_top_bottom_optimization.py` | 前缀因果、样本门槛、组合评价等测试 |
| `pipeline/precision_config.py`、`research_algorithms.py`、`pivot_algorithms.py`、`swing_signal_engine.py` | 区间/数据、均线反转与 Wilson、因果拐点、三层基础设施 |
| `pipeline/bigwave_bottom_engine.py` | 当前生产底部基线，本轮未改 |
| `data/raw/indices-ths/*.csv`、`data/raw/sectors-ths-industry/*.csv` | 本轮七指数和行业宽度原始输入 |
| `data/research/top-bottom-optimization-grid.csv` | 全部 6,468 行结果，含基线 |
| `data/research/top-bottom-optimization-results.json` | 首选、组合、事件和 5,296 条候选三层审计 |
| `data/research/top-bottom-optimization-report.md` | 含 Wilson 95% 区间、随机基准和基线的完整报告 |
| `requirements.txt` | Python 依赖 |

不要只复制本文档后继续训练；原始 CSV、完整网格、JSON 和研究代码都是复现所必需的。

### 9.3 本轮冻结数据与评价协议

本节是 2026-11-17 轮次的专用协议；与 §7 的旧默认区间/隔离天数不同时，本轮复现以本节及结果 JSON 的 `protocol` 为准，不得悄然混用。

- 七指数：`sh000001`、`sz399001`、`sz399006`、`sh000688`、`sh000300`、`sh000905`、`sh000852`。
- 数据截止：2026-11-17。
- 输入哈希：`255175b6957e0b63d30f3d991f58a9ea3f5fba5b5db070b894e6cfcea27e577b`；同时覆盖指数和行业宽度 CSV，回测前后变化会停止输出。
- 训练：`2020-10-30~2024-11-19`；训练末尾隔离 30 个交易日；验证：`2025-01-01~2026-11-17`。
- 复现时先使用交接的原始 CSV，不要先联网刷新行情，否则输入哈希和结果都会改变。
- 固定参数：`HORIZON=60`、`COOLDOWN=20`、`MIN_TRAIN_RESOLVED=20`、候选检测后 1～3 日确认、信号价为确认日收盘。
- 三层确认网格：

```json
[
  {"volume_ratio": 1.2, "breadth_improve": 0.05},
  {"volume_ratio": 1.0, "breadth_improve": 0.05},
  {"volume_ratio": 1.2, "breadth_improve": 0.03},
  {"volume_ratio": 0.8, "breadth_improve": 0.03}
]
```

两套准确率不得混用：

1. **固定 60 日单方向**：信号后 60 个交易日内首次出现同向均线反转为成功；满 60 日未出现为失败；段尾不足 60 日且未成功为 pending，pending 不进入分母。
2. **项目原口径**：同向均线反转必须先于实际反向信号；必须使用实际顶底组合合并事件后复算，不能拿单方向 legacy 数字代替。
3. 准确率均为成功数/已判定数，总信号数另列；20 日收益、MAE/MFE 只作诊断，不进入分母。本轮不是含交易成本的可交易净值回测。
4. 单方向入池：训练已判定至少 20 条且覆盖至少 2 年；不回退小样本。排序为训练 Wilson 下界 → 准确率 → 随机超额 → 样本量。
5. 实际组合要求原口径顶底训练已判定均至少 20 条；排序为 `min(顶/底训练 Wilson 下界)` → 顶底训练平均准确率 → 较小样本量。验证不参与排序。

### 9.4 完成量与固定 60 日首选

- 53 算法族、115 参数算法、4 组确认参数。
- 候选结果 `7 × 115 × 4 × 2 = 6,440` 行，另有 28 行基线，共 6,468 行。
- 13 个“指数×方向”达到固定 60 日正式样本门槛；上证顶部无正式候选。
- 6 个指数有实际顶底正式组合；上证无同时达标组合。

下表每格依次为：算法；训练准确率；验证准确率；训练/验证总信号次数。括号为成功数/已判定数，Wilson 区间见完整报告。

| 指数 | 底部首选 | 顶部首选 |
| --- | --- | --- |
| 上证 | KAMA10；90.0%（18/20）；100%（8/8）；20/9 次 | 无正式候选 |
| 深证成指 | 分形 k3；81.0%（17/21）；87.5%（7/8）；21/9 次 | 分形 k3；95.5%（21/22）；71.4%（5/7）；23/7 次 |
| 创业板指 | 分形 k5；81.0%（17/21）；100%（8/8）；21/8 次 | 分形 k2；95.2%（20/21）；72.7%（8/11）；22/11 次 |
| 科创50 | 分形 k2；68.0%（17/25）；91.7%（11/12）；25/13 次 | 分形 k5；100%（21/21）；100%（7/7）；21/7 次 |
| 沪深300 | 分形 k5；81.0%（17/21）；88.9%（8/9）；21/9 次 | 分形 k2；95.8%（23/24）；90.0%（9/10）；25/10 次 |
| 中证500 | 分形 k2；95.2%（20/21）；87.5%（7/8）；21/8 次 | 分形 k2；90.5%（19/21）；100%（7/7）；21/7 次 |
| 中证1000 | 分形 k3；85.0%（17/20）；100%（8/8）；20/9 次 | 分形 k2；90.5%（19/21）；75.0%（9/12）；22/12 次 |

上证顶部只有小样本观察项，不得与正式候选混排：

| 算法 | 训练 | 验证 | 未入池原因 |
| --- | ---: | ---: | --- |
| ElderRay20 | 100%（13/13，13 次） | 100%（3/3，3 次） | 训练已判定不足 20 |
| 分形 k2 | 94.1%（16/17，17 次） | 77.8%（7/9，9 次） | 训练已判定不足 20 |
| TSI(25,13,7) | 93.8%（15/16，17 次） | 83.3%（5/6，6 次） | 训练已判定不足 20 |

### 9.5 实际顶底组合首选、参数和原口径结果

以下不是生产配置。

| 指数 | 底部算法与三层参数 | 顶部算法与三层参数 |
| --- | --- | --- |
| 深证成指 | 分形 k3；`breadth_improve=0.03, volume_ratio=0.8` | 分形 k3；`breadth_improve=0.05, volume_ratio=1.2` |
| 创业板指 | 分形 k5；`breadth_improve=0.03, volume_ratio=0.8` | 分形 k2；`breadth_improve=0.03, volume_ratio=0.8` |
| 科创50 | 分形 k2；`breadth_improve=0.03, volume_ratio=0.8` | Donchian10；`breadth_improve=0.03, volume_ratio=0.8` |
| 沪深300 | 分形 k5；`breadth_improve=0.03, volume_ratio=0.8` | 分形 k2；`breadth_improve=0.03, volume_ratio=0.8` |
| 中证500 | 分形 k2；`breadth_improve=0.05, volume_ratio=1.0` | 分形 k2；`breadth_improve=0.03, volume_ratio=1.2` |
| 中证1000 | 分形 k3；`breadth_improve=0.03, volume_ratio=1.2` | 分形 k2；`breadth_improve=0.03, volume_ratio=0.8` |
| 上证 | 无同时满足顶底训练样本门槛的组合 | 同左 |

下表每格为训练准确率 → 验证准确率；训练/验证总信号次数：

| 指数 | 底部 | 顶部 |
| --- | --- | --- |
| 深证成指 | 57.1%（12/21）→ 44.4%（4/9）；21/9 次 | 68.2%（15/22）→ 42.9%（3/7）；23/7 次 |
| 创业板指 | 57.1%（12/21）→ 87.5%（7/8）；21/8 次 | 81.0%（17/21）→ 27.3%（3/11）；22/11 次 |
| 科创50 | 60.0%（15/25）→ 69.2%（9/13）；25/13 次 | 90.0%（18/20）→ 71.4%（5/7）；20/7 次 |
| 沪深300 | 52.4%（11/21）→ 88.9%（8/9）；21/9 次 | 66.7%（16/24）→ 60.0%（6/10）；25/10 次 |
| 中证500 | 66.7%（14/21）→ 87.5%（7/8）；21/8 次 | 81.0%（17/21）→ 85.7%（6/7）；21/7 次 |
| 中证1000 | 75.0%（15/20）→ 88.9%（8/9）；20/9 次 | 76.2%（16/21）→ 66.7%（8/12）；22/12 次 |

关键交叉核对：创业板顶部分形 k2 固定 60 日验证为 72.7%（8/11），实际组合原口径只有 27.3%（3/11）。后续不得只引用较高数字而隐去组合结果。

当前研究建议而非用户决定：中证500相对均衡；中证1000、科创50次之；沪深300观察；深证整组暂缓；创业板顶部不宜直接上线；上证顶部暂不上线正式信号。

### 9.6 新电脑复现命令

本轮实际使用 Python 3.13.15、pytest 9.2。系统 PATH 可能先有 Python 2.7，必须显式使用项目 `.venv`。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest pipeline/test_top_bottom_candidate_algorithms.py pipeline/test_top_bottom_optimization.py -q
```

单指数冒烟和完整复现均使用新输出名，避免覆盖交接产物：

```powershell
.\.venv\Scripts\python.exe pipeline/run_top_bottom_optimization.py --codes sh000688 --limit-specs 8 --output-stem reproduce-smoke
.\.venv\Scripts\python.exe pipeline/run_top_bottom_optimization.py --output-stem reproduce-round1
```

信号与研究回归：

```powershell
.\.venv\Scripts\python.exe -m pytest pipeline/test_confirmed_index_signals.py pipeline/test_confirmed_signals.py pipeline/test_swing_signal_engine.py pipeline/test_pivot_algorithms.py pipeline/test_research_algorithms.py pipeline/test_top_bottom_candidate_algorithms.py pipeline/test_top_bottom_optimization.py -q
```

### 9.7 后续续训规则

1. 先在新电脑复现输入哈希、6,468 行网格和研究测试，再新增算法。
2. 新算法在 `candidate_specs()` 注册，在 `candidate_points()` 返回 `(detection_index, reference_index)`；极值参考日与实际检测日必须分开。
3. 只能使用检测日及之前可知的数据；新增完整历史/截断历史前缀一致性测试。
4. 所有候选继续通过 `confirm_candidates()` 三层确认，不得为了提高胜率绕过确认、删除失败样本或放宽已判定样本门槛。
5. 不重复测试已有候选，除非有新的算法假设、新数据或新的预注册评价设计，并明确说明增量。
6. 新一轮使用 `--output-stem top-bottom-optimization-round2` 等新文件名，不覆盖本轮产物。
7. 比较新旧轮次时，先冻结同一输入验证代码复现，再单独报告新增行情后的结果，不能把数据变化和算法变化混成一次比较。
8. 每次研究结束，无论通过、淘汰或观察，都必须把样本区间、样本量、结论和留痕路径追加到本文档。
9. 只有用户明确选型后，才能修改生产信号和【指数总览】页面。

### 9.8 结果 JSON 速查与测试状态

- `selections[code][kind].options`：单方向候选。
- `selections[code][kind].chosen_events`：首选事件和逐日三层审计。
- `selections[code].pair_choice.chosen/options`：实际组合首选和最多 5 个备选。
- `untested`：缺少 point-in-time 数据而未测试的方法及原因。
- 本轮信号与研究回归：54 passed、3 skipped、115 subtests passed；115 个参数算法全部通过前缀因果测试。
- 全项目最后一轮：546 passed、87 failed、5 skipped、213 subtests passed。失败集中于既有行业历史接口、数据契约和 precision 因果测试；本轮新增顶底研究文件没有失败项。precision 的两个既有因果失败在后续修改 precision 时不得忽略。
- 约 9,900 条 pandas 布尔取反 DeprecationWarning 不是本轮失败原因。

### 9.9 本轮代码与结果 SHA-256

用于确认另一台电脑拿到同一版本；后续合法修改后应另登记新版本，不要强行保持哈希不变。

| 文件 | SHA-256 |
| --- | --- |
| `pipeline/top_bottom_candidate_algorithms.py` | `177600c2fe1c457cd405ff6df6472442f7e69aee79d09bbb2d09ea411f06db03` |
| `pipeline/run_top_bottom_optimization.py` | `149e447e4b53e812739df80bf526b88bf3016a1ac921ac93719cc1e235ceab50` |
| `pipeline/test_top_bottom_candidate_algorithms.py` | `70805e34672151e2ef9640ff56078b6cec6315f456575ddd83691665f72594fe` |
| `pipeline/test_top_bottom_optimization.py` | `f145be6a13805a5fd347eb43fedd4a9b9151910932df99658edc2bae7be2dbcc` |
| `data/research/top-bottom-optimization-grid.csv` | `ec0eb7d4900ff05cdb3f6673d51a72eb6d76e191721b2afca8702c2298b3b31a` |
| `data/research/top-bottom-optimization-results.json` | `772a51e60d8cec1aec7b8692a8e975ed0c907013446b57881da471a1960eb8e4` |
| `data/research/top-bottom-optimization-report.md` | `b36651bbb33572958792d251e1bdeb8830bdaa36e1c45445b60152cd7287437d` |
