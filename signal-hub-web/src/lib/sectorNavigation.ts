export type SectorNavKey = 'code' | 'state' | 'sort' | 'q';

/** 计算板块页 URL 参数：板块单层，选中项与筛选/排序/检索互不冲突，后者变化即清空选中。 */
export function nextSectorParams(
  current: URLSearchParams,
  key: SectorNavKey,
  value: string,
): URLSearchParams {
  const next = new URLSearchParams(current);
  if (value) next.set(key, value);
  else next.delete(key);
  if (key !== 'code') {
    next.delete('code');
  }
  return next;
}
