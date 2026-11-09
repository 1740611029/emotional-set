/** 多持有期矩阵的前端聚合（纯计算，不依赖 React，便于单元测试直接运行）。 */
import type {
  PrecisionHorizon, PrecisionSegment, SignalKind, Snapshot,
} from '../data/types';

export const PRECISION_HORIZONS: readonly PrecisionHorizon[] = ['5', '10', '20', '30'];
export const PRECISION_KINDS: readonly SignalKind[] = ['bottom', 'top'];

export interface MatrixCellView {
  available: boolean;
  triggers: number;
  resolved: number;
  successes: number;
  failures: number;
  pending: number;
  directionRate: number | null;
  amplitudeRate: number | null;
  /** 方向正确率的 Wilson 95% 区间，0-1 小数 */
  wilson: [number | null, number | null];
  avgMae: number | null;
  maeFailures: number;
  smallSample: boolean;
  singleDigit: boolean;
  interval: { start: string | null; end: string | null };
  amplitudeLine: number | null;
}

export type MatrixView = Record<PrecisionHorizon, Record<SignalKind, MatrixCellView>>;

/** Wilson 95% 区间；z 与后端 research_algorithms.wilson_interval 完全一致。 */
export function wilsonInterval(successes: number, total: number): [number | null, number | null] {
  if (total <= 0) return [null, null];
  const z = 1.959963984540054;
  const p = successes / total;
  const denom = 1 + (z * z) / total;
  const center = (p + (z * z) / (2 * total)) / denom;
  const half = (z / denom) * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total));
  return [Math.max(0, center - half), Math.min(1, center + half)];
}

function emptyCell(): MatrixCellView {
  return {
    available: false, triggers: 0, resolved: 0, successes: 0, failures: 0, pending: 0,
    directionRate: null, amplitudeRate: null, wilson: [null, null], avgMae: null,
    maeFailures: 0, smallSample: false, singleDigit: false,
    interval: { start: null, end: null }, amplitudeLine: null,
  };
}

/** 把 7 个指数的矩阵按段合并；无数据的格一律 available=false，页面显示 `--` 而非 0%。 */
export function precisionMatrix(
  snapshot: Snapshot | null,
  segment: PrecisionSegment,
): MatrixView {
  const view = {} as MatrixView;
  for (const horizon of PRECISION_HORIZONS) {
    view[horizon] = { bottom: emptyCell(), top: emptyCell() };
  }
  if (!snapshot) return view;

  for (const horizon of PRECISION_HORIZONS) {
    for (const kind of PRECISION_KINDS) {
      const cells = snapshot.indices
        .map((item) => item.precision_matrix?.[segment]?.horizons?.[horizon]?.[kind])
        .filter((cell): cell is NonNullable<typeof cell> => Boolean(cell));
      if (cells.length === 0) continue;
      const totals = cells.reduce(
        (acc, cell) => ({
          triggers: acc.triggers + cell.triggers,
          resolved: acc.resolved + cell.resolved,
          successes: acc.successes + cell.successes,
          failures: acc.failures + cell.failures,
          pending: acc.pending + cell.pending,
          amplitudeSuccesses: acc.amplitudeSuccesses + cell.effective_amplitude_successes,
          maeFailures: acc.maeFailures + cell.drawdown.mae_exceeded_failures,
        }),
        { triggers: 0, resolved: 0, successes: 0, failures: 0, pending: 0,
          amplitudeSuccesses: 0, maeFailures: 0 },
      );
      let maeWeighted = 0;
      let maeWeight = 0;
      for (const cell of cells) {
        const value = cell.drawdown.avg_max_adverse_excursion;
        if (value !== null && value !== undefined && cell.resolved > 0) {
          maeWeighted += value * cell.resolved;
          maeWeight += cell.resolved;
        }
      }
      const interval = cells[0].statistical_interval;
      const line = cells[0].effective_amplitude_line;
      view[horizon][kind] = {
        available: true,
        triggers: totals.triggers,
        resolved: totals.resolved,
        successes: totals.successes,
        failures: totals.failures,
        pending: totals.pending,
        directionRate: totals.resolved > 0 ? totals.successes / totals.resolved : null,
        amplitudeRate: totals.resolved > 0 ? totals.amplitudeSuccesses / totals.resolved : null,
        wilson: wilsonInterval(totals.successes, totals.resolved),
        avgMae: maeWeight > 0 ? maeWeighted / maeWeight : null,
        maeFailures: totals.maeFailures,
        smallSample: totals.resolved < 30,
        singleDigit: totals.resolved < 10,
        interval: { start: interval?.start ?? null, end: interval?.end ?? null },
        amplitudeLine: line ?? null,
      };
    }
  }
  return view;
}
