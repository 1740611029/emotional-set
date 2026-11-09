/** 板块四态方向评估的展示计算；不依赖 React，便于单独测试。 */
import type {
  SectorEvalHorizon,
  SectorEvaluation,
  SectorEvaluationSegment,
  SectorState,
  SectorStateEvaluation,
} from '../data/types';
import { STATE_LABELS, STATE_ORDER } from './sectorStates.ts';

/** 前瞻窗口的展示顺序：先 10 日后 20 日 */
export const HORIZONS: SectorEvalHorizon[] = ['10', '20'];

export const SEGMENT_LABELS: Record<SectorEvaluationSegment['segment'], string> = {
  research: '研究段',
  exploratory_validation: '验证段',
};

/** 千分位分组；不依赖 Intl，保证 node 测试与浏览器输出一致 */
export function groupThousands(value: number): string {
  const text = String(Math.trunc(Math.abs(value)));
  const grouped = text.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return value < 0 ? `-${grouped}` : grouped;
}

/** 胜率按小数转百分数，保留两位；无已判定样本时给占位符 */
export function formatRate(rate: number | null): string {
  return rate === null ? '—' : `${(rate * 100).toFixed(2)}%`;
}

export function formatSamples(samples: number): string {
  return `n=${groupThousands(samples)}`;
}

/** Wilson 区间与胜率同单位，任一端缺失即视为无区间 */
export function formatInterval(interval: [number | null, number | null]): string {
  const [low, high] = interval;
  if (low === null || high === null) return '—';
  return `${(low * 100).toFixed(2)}%–${(high * 100).toFixed(2)}%`;
}

export interface EvaluationCellView {
  horizon: SectorEvalHorizon;
  horizonLabel: string;
  /** 有已判定样本才会显示数值，否则整格显示占位 */
  hasSamples: boolean;
  rateText: string;
  sampleText: string;
  intervalText: string;
  smallSample: boolean;
}

export interface EvaluationRowView {
  state: SectorState;
  label: string;
  cells: EvaluationCellView[];
}

export interface EvaluationSegmentView {
  segment: SectorEvaluationSegment['segment'];
  label: string;
  /** 独立验证为 false 时页面需提示「事后观察」 */
  independent: boolean;
  rows: EvaluationRowView[];
}

/** 单格归一：null（该状态在该窗口没有事件）与「有事件但全部未决」都落到占位显示 */
export function buildCellView(
  horizon: SectorEvalHorizon,
  cell: SectorStateEvaluation | null,
): EvaluationCellView {
  const hasSamples = Boolean(cell) && (cell as SectorStateEvaluation).samples > 0;
  return {
    horizon,
    horizonLabel: `${horizon} 日`,
    hasSamples,
    rateText: hasSamples ? formatRate((cell as SectorStateEvaluation).rate) : '—',
    sampleText: formatSamples(cell ? cell.samples : 0),
    intervalText: hasSamples ? formatInterval((cell as SectorStateEvaluation).wilson95) : '—',
    smallSample: Boolean(cell && cell.small_sample),
  };
}

/** 段内行序固定为 STATE_ORDER（多头 → 反转 → 退潮 → 空头），四态缺一不渲染 */
export function buildSegmentView(segment: SectorEvaluationSegment): EvaluationSegmentView {
  return {
    segment: segment.segment,
    label: SEGMENT_LABELS[segment.segment] ?? segment.segment,
    independent: segment.independent,
    rows: STATE_ORDER.map((state) => ({
      state,
      label: STATE_LABELS[state],
      cells: HORIZONS.map((horizon) => buildCellView(horizon, segment.states[state]?.[horizon] ?? null)),
    })),
  };
}

/** 评估未生成或结构为空时返回空数组，页面据此渲染「评估尚未生成」 */
export function buildEvaluationViews(
  evaluation: SectorEvaluation | null | undefined,
): EvaluationSegmentView[] {
  if (!evaluation || !Array.isArray(evaluation.segments)) return [];
  return evaluation.segments
    .filter((segment) => segment && segment.states)
    .map((segment) => buildSegmentView(segment));
}
