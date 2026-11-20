/** 图表时间范围工具：由 ETF 放量页复用。
 * 指数总览页已于 2026-11-20 下线，其信号图表构建函数一并移除。 */

export type IndexChartRangeKey = '1m' | '3m' | '6m' | '1y' | '2y' | '3y' | 'all';

export interface IndexChartRangeOption {
  key: IndexChartRangeKey;
  label: string;
  tradingDays: number | null;
}

export const INDEX_CHART_RANGES: readonly IndexChartRangeOption[] = [
  { key: '1m', label: '近一个月', tradingDays: 22 },
  { key: '3m', label: '近三个月', tradingDays: 66 },
  { key: '6m', label: '近半年', tradingDays: 126 },
  { key: '1y', label: '近一年', tradingDays: 252 },
  { key: '2y', label: '近两年', tradingDays: 504 },
  { key: '3y', label: '近三年', tradingDays: 756 },
  { key: 'all', label: '全部', tradingDays: null },
];

export const DEFAULT_INDEX_CHART_RANGE_KEY: IndexChartRangeKey = '1y';

export function indexChartRangeOption(key: IndexChartRangeKey): IndexChartRangeOption {
  const option = INDEX_CHART_RANGES.find((item) => item.key === key);
  if (!option) throw new Error(`未知指数图表时间范围：${key}`);
  return option;
}
