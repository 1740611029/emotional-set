// 机构历史明细列表的纯逻辑：日期倒序筛选、固定高度滚动加载与重置判定
// 对应 spec: openspec/specs/institution-position-view/spec.md「历史明细列表」

export const HISTORY_PAGE_SIZE = 50;
export const HISTORY_TABLE_HEIGHT_PX = 250;

export interface InstitutionHistoryDateRange {
  from: string;
  to: string;
}

/**
 * 从全量交易日（升序）中筛选区间内的日期，并按机构取序列后生成倒序行索引。
 * 只返回落在 [from, to] 内、且该机构有视图数据的日期索引（日期倒序）。
 */
export function selectHistoryIndexes(
  dates: string[],
  range: InstitutionHistoryDateRange,
  hasSeries: boolean,
): number[] {
  if (!hasSeries) return [];
  const indexes: number[] = [];
  for (let index = dates.length - 1; index >= 0; index -= 1) {
    const date = dates[index];
    if (range.from && date < range.from) continue;
    if (range.to && date > range.to) continue;
    indexes.push(index);
  }
  return indexes;
}

/** 当前筛选下可视行数：首批最多 HISTORY_PAGE_SIZE 行（默认倒序与分页场景）。 */
export function initialVisibleCount(totalRows: number): number {
  return Math.min(HISTORY_PAGE_SIZE, totalRows);
}

/**
 * 滚动加载触发条件：滚动位置距容器底部小于 thresholdPx 且仍有未展示行。
 */
export function shouldLoadMore(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
  visibleCount: number,
  totalRows: number,
  thresholdPx = 40,
): boolean {
  if (visibleCount >= totalRows) return false;
  return scrollHeight - scrollTop - clientHeight < thresholdPx;
}

/** 加载下一批后的可视行数，封顶 totalRows。 */
export function nextVisibleCount(visibleCount: number, totalRows: number): number {
  return Math.min(visibleCount + HISTORY_PAGE_SIZE, totalRows);
}

/** 是否仍有未加载的数据（决定「向下滚动加载更多」提示是否出现）。 */
export function hasMoreToLoad(visibleCount: number, totalRows: number): boolean {
  return visibleCount < totalRows;
}

/** 底部进度提示文案：「共 X 行，已展示 Y 行」。 */
export function historyProgressLabel(totalRows: number, visibleRows: number): string {
  return `共 ${totalRows} 行，已展示 ${visibleRows} 行`;
}
