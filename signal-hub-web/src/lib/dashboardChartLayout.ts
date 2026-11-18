/** 【指标总览】图表主区的展示顺序：仅影响前端渲染排列，不改动任何数据产物。 */

/**
 * 展示顺序（2026-11-18 随指数范围收窄为 3 只）：
 * 第一行 上证指数；第二行 科创50 + 创业板指。
 * 深证成指、沪深300、中证500、中证1000 已从生产信号与总览中移除。
 */
export const DASHBOARD_INDEX_CHART_ORDER: readonly string[] = [
  'sh000001', // 上证指数（独占首行）
  'sh000688', // 科创50
  'sz399006', // 创业板指
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
