/** 板块成分股（弹窗）排序：涨停时间 / 连板数 / 封单金额 / 成交额 / 涨幅 五维；
 * 非涨停股（limit 为 null）恒置尾；涨停股三列缺失值同样置尾、与方向解耦。
 * 涨停时间语义：越早封板越强（09:25 一字板最强），因此「强在前」= 时间升序。 */
import type { SectorStock } from '../data/types';

export type SectorStockSortKey = 'first_seal' | 'boards' | 'seal_amount' | 'amount' | 'change_pct';
export type SectorStockSortDirection = 'desc' | 'asc';

/** 可排序列的展示顺序；除涨停时间外第一下恒为降序（数值大在前） */
export const SECTOR_STOCK_SORT_KEYS: SectorStockSortKey[] = [
  'first_seal',
  'boards',
  'seal_amount',
  'amount',
  'change_pct',
];

/** first_seal 默认方向为升序（早封板在前）；其余列默认降序 */
export function defaultDirection(key: SectorStockSortKey): SectorStockSortDirection {
  return key === 'first_seal' ? 'asc' : 'desc';
}

export function nextDirection(current: SectorStockSortDirection): SectorStockSortDirection {
  return current === 'desc' ? 'asc' : 'desc';
}

function isLimitUp(stock: SectorStock): boolean {
  return Boolean(stock.limit);
}

/** first_seal/boards/seal_amount 在 limit 子对象上，amount/change_pct 在顶层 */
function valueOf(stock: SectorStock, key: SectorStockSortKey): unknown {
  if (key === 'first_seal' || key === 'boards' || key === 'seal_amount') {
    return stock.limit ? stock.limit[key] : null;
  }
  return stock[key];
}

/** 空值置尾与排序方向解耦：null 无论升序降序都排在最后 */
function compareByKey(
  x: SectorStock,
  y: SectorStock,
  key: SectorStockSortKey,
  direction: SectorStockSortDirection,
): number {
  // 非涨停股恒置尾：涨停股在前，非涨停股在后（升序/降序均不影响这一分组）
  const xUp = isLimitUp(x);
  const yUp = isLimitUp(y);
  if (xUp !== yUp) return xUp ? -1 : 1;

  const xv = valueOf(x, key);
  const yv = valueOf(y, key);
  const xNull = xv == null;
  const yNull = yv == null;
  if (xNull && yNull) return 0;
  if (xNull) return 1;
  if (yNull) return -1;
  // first_seal 为 "HH:MM" 字符串：升序 = 早封板在前（强在前）
  if (key === 'first_seal') {
    const cmp = (xv as string) < (yv as string) ? -1 : (xv as string) > (yv as string) ? 1 : 0;
    return direction === 'asc' ? cmp : -cmp;
  }
  return direction === 'desc' ? (yv as number) - (xv as number) : (xv as number) - (yv as number);
}

export function sortSectorStocks(
  stocks: SectorStock[],
  key: SectorStockSortKey,
  direction: SectorStockSortDirection,
): SectorStock[] {
  return [...stocks].sort((x, y) => compareByKey(x, y, key, direction));
}