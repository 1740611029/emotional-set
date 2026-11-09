/** 【指标总览】多持有期准确率矩阵：替换原先单一「已判定胜率」卡片。 */
import { useState } from 'react';
import type { Snapshot, PrecisionSegment, PrecisionHorizon, SignalKind } from '../data/types';
import {
  PRECISION_HORIZONS, PRECISION_KINDS, precisionMatrix,
  type MatrixCellView,
} from '../lib/precisionMatrix';

const SEGMENTS: { key: PrecisionSegment; label: string }[] = [
  { key: 'train', label: '研究段' },
  { key: 'validation', label: '独立验证段' },
];

const KIND_LABELS: Record<SignalKind, string> = { bottom: '底部信号', top: '顶部信号' };

function pct(value: number | null): string {
  return value === null || !Number.isFinite(value) ? '--' : `${(value * 100).toFixed(1)}%`;
}

function intervalText(cell: MatrixCellView): string {
  const [low, high] = cell.wilson;
  if (low === null || high === null) return '--';
  return `${(low * 100).toFixed(1)}% ~ ${(high * 100).toFixed(1)}%`;
}

function MatrixCell({ cell, horizon, kind }: {
  cell: MatrixCellView;
  horizon: PrecisionHorizon;
  kind: SignalKind;
}) {
  const tint = kind === 'bottom' ? 'text-mint-600' : 'text-coral-500';
  return (
    <div className="rounded-xl border border-slate-100 bg-white/70 p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-slate-600">
          {horizon} 个交易日 · {KIND_LABELS[kind]}
        </span>
        <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium tabular-nums ${cell.available ? 'bg-brand-50 text-brand-700' : 'bg-slate-100 text-slate-400'}`}>
          n={cell.triggers}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <p className="text-[11px] text-slate-400">方向正确率</p>
          <p className={`text-lg font-bold tabular-nums ${cell.available ? tint : 'text-slate-400'}`}>
            {cell.available ? pct(cell.directionRate) : '--'}
          </p>
        </div>
        <div>
          <p className="text-[11px] text-slate-400">有效幅度率</p>
          <p className={`text-lg font-bold tabular-nums ${cell.available ? 'text-ocean-600' : 'text-slate-400'}`}>
            {cell.available ? pct(cell.amplitudeRate) : '--'}
          </p>
        </div>
      </div>

      <div className="mt-2 space-y-0.5 text-[11px] text-slate-500 tabular-nums">
        <p>已判定 {cell.resolved} · 成功 {cell.successes} · 失败 {cell.failures} · 未决 {cell.pending}</p>
        <p>Wilson 95% {cell.available ? intervalText(cell) : '--'}</p>
        <p>平均最大不利偏移 {cell.available ? pct(cell.avgMae) : '--'} · 浮亏超限失败 {cell.maeFailures}</p>
        <p>统计区间 {cell.interval.start && cell.interval.end
          ? `${cell.interval.start} ~ ${cell.interval.end}` : '--'}</p>
        {cell.available && cell.amplitudeLine !== null && (
          <p>本格幅度线 {(cell.amplitudeLine * 100).toFixed(2)}%</p>
        )}
        {cell.available && cell.smallSample && (
          <p className="text-coral-500">
            样本量较小，仅供参考{cell.singleDigit ? '（已判定为个位数样本）' : ''}
          </p>
        )}
      </div>
    </div>
  );
}

export function AccuracyMatrixPanel({ snapshot, bare = false, onClose }: {
  snapshot: Snapshot;
  bare?: boolean;
  onClose?: () => void;
}) {
  const [segment, setSegment] = useState<PrecisionSegment>('validation');
  const matrix = precisionMatrix(snapshot, segment);
  const precision = snapshot.precision ?? null;
  const universe = precision?.covered_universe ?? snapshot.indices.map((item) => item.code);
  const names = universe.map(
    (code) => snapshot.indices.find((item) => item.code === code)?.name ?? code,
  );
  const segmentInfo = precision?.segments?.[segment] as
    | { start?: string; end?: string; effective_start?: string; effective_end?: string }
    | undefined;

  return (
    <section
      className={bare ? 'flex flex-col h-full min-h-0' : 'glass-card rounded-2xl p-6 mb-8 fade-in'}
      style={bare ? undefined : { animationDelay: '0.12s' }}
    >
      {/* 固定头部：标题 + 分段切换，弹窗内滚动时保持不动 */}
      <div className={bare ? 'shrink-0 px-6 pt-5 pb-4 border-b border-slate-100' : ''}>
        <div className={bare ? 'flex items-start justify-between gap-3' : 'flex flex-wrap items-start justify-between gap-3 mb-4'}>
          <div>
            <h3 className="text-lg font-bold text-slate-800">多持有期准确率矩阵</h3>
            <p className="text-xs text-slate-400 mt-1">
              按 5 / 10 / 20 / 30 个交易日分别统计；每格都带触发次数与样本量，不再只给一个百分比
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex gap-1 p-1 rounded-lg bg-slate-50">
              {SEGMENTS.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setSegment(option.key)}
                  aria-pressed={segment === option.key}
                  className={`px-3 py-1.5 text-xs rounded-md font-medium transition-colors ${
                    segment === option.key
                      ? 'bg-brand-500 text-white'
                      : 'text-slate-500 hover:bg-brand-50 hover:text-brand-700'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭准确率详情"
                className="w-8 h-8 rounded-full bg-slate-100 text-slate-500 hover:text-slate-800 hover:bg-slate-200 text-lg leading-none flex items-center justify-center"
              >
                ×
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 可滚动主体：仅此区域滚动，头部保持固定 */}
      <div className={bare ? 'flex-1 overflow-y-auto min-h-0 px-6 py-4' : ''}>
      <div className="rounded-xl bg-ocean-50/70 border border-ocean-100 px-4 py-3 mb-4">
        <p className="text-xs font-semibold text-ocean-700">
          统计覆盖 {universe.length} 个标的
          {precision ? `（口径 ${precision.algorithm_version}）` : '（矩阵数据尚未就绪）'}
        </p>
        <p className="text-[11px] text-ocean-700/80 mt-1">{names.join('、')}</p>
        <p className="text-[11px] text-slate-500 mt-1 tabular-nums">
          {segment === 'train' ? '研究段' : '独立验证段'}
          {segmentInfo?.start && segmentInfo?.end
            ? ` ${segmentInfo.start} ~ ${segmentInfo.end}`
            : ''}
          {' · '}下图 7 张指数图卡是展示范围，与统计覆盖范围一致
        </p>
      </div>

      <div className="space-y-4">
        {PRECISION_HORIZONS.map((horizon) => (
          <div key={horizon} className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {PRECISION_KINDS.map((kind) => (
              <MatrixCell
                key={`${horizon}-${kind}`}
                cell={matrix[horizon][kind]}
                horizon={horizon}
                kind={kind}
              />
            ))}
          </div>
        ))}
      </div>

      <div className="mt-5 rounded-xl bg-mint-50/70 border border-mint-100 px-4 py-3">
        <p className="text-xs font-semibold text-brand-700">浮亏约束，用大白话说</p>
        <p className="text-[11px] text-slate-600 mt-1 leading-relaxed">
          信号发出后，中途最多允许浮亏 5%，超出就判失败，不管最后涨没涨回来。
          比如 3000 点发出的底部信号，之后 20 个交易日里最低不能跌破 2850 点；
          跌到 2849 点就算失败。这里用的是盘中最低价（顶部信号看盘中最高价），不是收盘价，
          所以更严。方向正确率还要看第 5 / 10 / 20 / 30 个交易日的收盘是不是方向对了；
          有效幅度率进一步要求涨跌幅度达到该指数、该持有期自己的线——大盘波动小、线就低，
          创业板波动大、线就高。
        </p>
      </div>

        <p className="text-[11px] text-slate-400 mt-3 leading-relaxed">
          {precision?.criteria_note ?? '固定持有期方向正确 + 20 日盘中浮亏硬约束。'}
          {' '}{precision?.legacy_rate_note ?? ''}
          {' '}未决样本不计入分母但数量可见；方向准确率是统计描述，不代表交易收益率，也不构成任何买卖建议。
        </p>
      </div>
    </section>
  );
}
