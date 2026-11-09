/** 板块趋势的纯计算与文案映射；不依赖 React，便于单独测试。 */
import type { SectorState, SectorStateBasis, SectorStrengthParts } from '../data/types';

export const STATE_ORDER: SectorState[] = ['bull', 'rise', 'warn', 'bear'];

export const STATE_LABELS: Record<SectorState, string> = {
  bull: '多头',
  rise: '反转',
  warn: '退潮',
  bear: '空头',
};

/** 状态对应的语义说明，用于详情与图例 */
export const STATE_NOTES: Record<SectorState, string> = {
  bull: '收盘在 20 日区间中轨之上且高于 MA60',
  rise: '上穿 20 日区间中轨，但仍低于 MA60',
  warn: '跌破 20 日区间中轨，但仍高于 MA60',
  bear: '收盘在 20 日区间中轨之下且低于 MA60',
};

export const STATE_TEXT_CLASS: Record<SectorState, string> = {
  bull: 'bg-mint-50 text-mint-700',
  rise: 'bg-ocean-50 text-ocean-700',
  warn: 'bg-coral-50 text-coral-600',
  bear: 'bg-coral-50 text-coral-600',
};

export const STATE_COLORS: Record<SectorState, string> = {
  bull: '#14b8a6',
  rise: '#3a90f5',
  warn: '#ffaa80',
  bear: '#f26522',
};

export interface StateSegment {
  from: string;
  to: string;
  state: SectorState;
}

/** 把逐日状态压缩成连续区间；空状态不入区间。 */
export function compressStates(
  dates: string[],
  states: (SectorState | null)[],
): StateSegment[] {
  const segments: StateSegment[] = [];
  const total = Math.min(dates.length, states.length);
  for (let index = 0; index < total; index += 1) {
    const state = states[index];
    if (!state) continue;
    const last = segments[segments.length - 1];
    if (last && last.state === state && last.to === dates[index - 1]) {
      last.to = dates[index];
      continue;
    }
    segments.push({ from: dates[index], to: dates[index], state });
  }
  return segments;
}

/** 简单移动平均；窗口内存在缺失值时不输出。 */
export function movingAverage(values: (number | null)[], window: number): (number | null)[] {
  const result: (number | null)[] = [];
  let sum = 0;
  let missing = 0;
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value === null) missing += 1;
    else sum += value;
    if (index >= window) {
      const drop = values[index - window];
      if (drop === null) missing -= 1;
      else sum -= drop;
    }
    result.push(index >= window - 1 && missing === 0 ? sum / window : null);
  }
  return result;
}

export interface StrengthExplanation {
  average: number;
  expected: number;
  formula: string;
  basisLabel: string;
}

/** 强度构成的可复算算式；与产物校验口径一致。 */
export function explainStrength(
  parts: SectorStrengthParts,
  basis: SectorStateBasis | null,
): StrengthExplanation {
  const average = (parts.range_position + parts.ma_score) / 2;
  if (basis === 'neutral') {
    return {
      average,
      expected: 0,
      formula: `(${parts.range_position.toFixed(1)} + ${parts.ma_score.toFixed(1)}) ÷ 2 − 50；通道宽度为零，固定为 0`,
      basisLabel: '中性（通道宽度为零）',
    };
  }
  const expected = average - 50;
  return {
    average,
    expected,
    formula: `(${parts.range_position.toFixed(1)} + ${parts.ma_score.toFixed(1)}) ÷ 2 − 50 = ${expected.toFixed(1)}`,
    basisLabel: '有向（所有状态统一公式，不取反）',
  };
}

export type SectorSortKey = 'strength' | 'name';

/** 板块排序：有向强度按原始有符号值降序，空值与数据异常项置尾。 */
export function sortSectorItems<T extends {
  name: string;
  strength: number | null;
  data_anomaly: boolean;
}>(items: T[], sort: SectorSortKey): T[] {
  const copy = [...items];
  if (sort === 'name') {
    return copy.sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'));
  }
  const rank = (item: T) => item.strength === null || item.data_anomaly ? null : item.strength;
  return copy.sort((left, right) => {
    const leftRank = rank(left);
    const rightRank = rank(right);
    if (leftRank === null && rightRank === null) return left.name.localeCompare(right.name, 'zh-CN');
    if (leftRank === null) return 1;
    if (rightRank === null) return -1;
    return rightRank - leftRank;
  });
}

export function formatStrength(value: number | null): string {
  return value === null ? '—' : value.toFixed(1);
}

export function formatDays(value: number | null): string {
  return value === null ? '—' : `${value} 日`;
}
