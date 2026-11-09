/** 【指标总览】图表主区的展示顺序：仅影响前端渲染排列，不改动任何数据产物。 */

/**
 * 桌面四行分组（1+2+2+2）对应的指数 code 顺序：
 * 第一行 上证指数；第二行 科创50 + 创业板指；
 * 第三行 深证成指 + 沪深300；第四行 中证500 + 中证1000。
 */
export const DASHBOARD_INDEX_CHART_ORDER: readonly string[] = [
  'sh000001', // 上证指数（独占首行）
  'sh000688', // 科创50
  'sz399006', // 创业板指
  'sz399001', // 深证成指
  'sh000300', // 沪深300
  'sh000905', // 中证500
  'sh000852', // 中证1000
];

/**
 * 按 DASHBOARD_INDEX_CHART_ORDER 重排指数用于图表展示：
 * 已知 code 依目标顺序排列，未知 code 保留原始相对顺序追加到末尾；
 * 快照缺项时只返回现有项。返回新数组，不修改入参。
 */
export function orderIndicesForCharts<T extends { code: string }>(indices: readonly T[]): T[] {
  const rank = new Map(DASHBOARD_INDEX_CHART_ORDER.map((code, index) => [code, index]));
  const known: T[] = [];
  const unknown: T[] = [];
  for (const item of indices) {
    if (rank.has(item.code)) known.push(item);
    else unknown.push(item);
  }
  known.sort((left, right) => rank.get(left.code)! - rank.get(right.code)!);
  return [...known, ...unknown];
}
