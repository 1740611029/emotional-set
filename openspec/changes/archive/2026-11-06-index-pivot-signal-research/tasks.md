# Tasks

## 1. 真实拐点基准集（pivot_benchmark）

- [x] 1.1 新建 `pipeline/pivot_benchmark.py`：逐日因果滚动 ZigZag 拐点标注纯函数（幅度阈值 + 最小间隔参数化），拐点仅在其后反向运动达标时事后写入；输出逐指数 date/type/price 列表
- [x] 1.2 编写 `pipeline/test_pivot_benchmark.py`：人工小序列上验证拐点日期/类型/价格逐值正确，且"反转未达标则不标注"场景通过
- [x] 1.3 在训练段（2020-10-30~2024-12-31）对 7 个指数跑阈值网格 {3%,5%,7%}×{8,13,21} 日，按"年均拐点 4–8 个"约束选出一组冻结参数，并记录到模块级 `FROZEN_PARAMS` 常量与注释（确定区间写明）
- [x] 1.4 生成 `data/research/index-pivot-benchmark.json`（含冻结参数、逐指数区间、input_hash 的 manifest 字段）；抽样 3 个拐点与 K 线目视核对并记录核对结果

## 2. 拐点口径评价模块（pivot_eval）

- [x] 2.1 新建 `pipeline/pivot_eval.py`：信号集×基准集 → 捕捉率（±M 日）、滞后分布（中位数/P90）、点位偏差分布、主达标率（确认后≤5 交易日落在拐点±N 日）；训练/验证段切分含 embargo，复用 `research_algorithms.wilson_interval`
- [x] 2.2 在训练段冻结 N、M（候选 M∈{3,5,8}，N 使"≤5 交易日确认"口径可判），冻结值写入模块常量并注明确定区间；验证段代码路径禁止读训练段以外数据调参
- [x] 2.3 编写 `pipeline/test_pivot_eval.py`：构造已知信号/拐点合成样本，四项指标逐值验证；已判定样本<30 时输出含小样本标注
- [x] 2.4 实现基准阈值 3%/5%/7% 三档敏感性输出（同一信号集在不同基准下的指标对照），验证报告结构含该节

## 3. 候选拐点算法与辅助因子

- [x] 3.1 新建 `pipeline/pivot_algorithms.py`：实现 pivot-zigzag、pivot-donchian（N∈{20,55}, M∈{3,5}）、pivot-fractal 三种因果拐点算法，统一输出"确认日"语义的信号记录
- [x] 3.2 编写截断重跑未来函数自检测试（`pipeline/test_pivot_algorithms.py`）：对每算法删除 T 日后数据重算，T 日及之前信号逐条一致
- [x] 3.3 实现变体 `pivot-zigzag+confirm3`：复用 `swing_signal_engine.layer_state` 三层确认对 zigzag 候选做确认，确认失败候选作废且留日志
- [x] 3.4 新建 `pipeline/pivot_aux_factors.py`：资金面（两融 20 日变化、ETF 份额变化率）、情绪面（QVIX 分位）、技术面（RSI/MACD 背离复用 `swing_indicators`）、宽度（breadth20 分位/delta5）、外围（隔夜标普500，仅用 T 日已发布值）辅助得分；每因子登记 P0/P1/P2 与出处，缺失记 NaN 不补值
- [x] 3.5 编写 `pipeline/test_pivot_aux_factors.py`：验证辅助得分只改变分级不改变信号集合（同输入下加分前后信号日期/类型/数量逐条一致），以及 T 日后数据置乱不影响 T 日前取值
- [x] 3.6 在训练段为每算法选主指标 Top-1 参数、为辅助因子选权重（{0.05,0.10,0.15}），冻结参数写入 `PREREGISTERED` 常量（含确定区间）

## 4. 对照入口、报告与生产保护

- [x] 4.1 新建 `pipeline/run_pivot_comparison.py`：串起基准集→全部候选（含变体与辅助增益版）→现行 `index-swing6-v2.0`（同基准同口径同确认日语义）→验证段一次性评估；产物 `data/research/index-pivot-comparison.json` 与 `.md`
- [x] 4.2 入口实现生产文件指纹防护：运行前后对 `data/index-signals.json`、`data/sector-trends.json`、`data/raw/indices-ths/*.csv` 做 sha256+mtime 比对，不一致立即报错退出
- [x] 4.3 报告生成逻辑：全候选并列（含淘汰候选及原因）、逐指标附样本量/统计区间/Wilson 区间、每候选给出"更换建议/观察/保留现行"三态结论与理由；验证段汇总样本<30 时候选结论强制不高于"观察"
- [x] 4.4 编写 `pipeline/test_pivot_comparison.py`：mock 小数据端到端跑通入口，断言全部产物落在 `data/research/`、生产文件指纹不变、报告含三态结论字段与全部候选条目

## 5. 集成验证与交付

- [x] 5.1 用真实数据全量运行 `python pipeline/run_pivot_comparison.py`，确认运行成功、生产 `data/index-signals.json` 内容前后逐字节一致（diff 为空）
- [x] 5.2 `pipeline` 下运行 `pytest` 全量测试（含新旧测试）全部通过，无对生产路径的导入副作用
- [x] 5.3 人工核对 `data/research/index-pivot-comparison.md`：每个比率均带样本量与统计区间、结论三态明确、附 3%/5%/7% 敏感性对照，交付给用户作为"是否更换算法"决策材料
