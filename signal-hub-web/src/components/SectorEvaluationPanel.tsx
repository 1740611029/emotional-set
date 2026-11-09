/** 板块四态方向胜率验证区块：研究段 / 验证段各一张卡，四态 × 10/20 日。 */
import { useMemo } from 'react';
import type { SectorEvaluation } from '../data/types';
import { buildEvaluationViews } from '../lib/sectorEvaluation';
import type { EvaluationCellView, EvaluationSegmentView } from '../lib/sectorEvaluation';
import { STATE_TEXT_CLASS } from '../lib/sectorStates';

const DEFAULT_NOTE = '板块趋势为描述性分类，方向胜率不代表交易收益；验证段为事后观察，不构成独立验证。';

function HorizonCell({ cell }: { cell: EvaluationCellView }) {
  return (
    <td className="px-2 py-2 align-top">
      <div className="flex items-baseline gap-1.5">
        <span className={`text-sm font-semibold tabular-nums ${cell.hasSamples ? 'text-slate-800' : 'text-slate-400'}`}>
          {cell.rateText}
        </span>
        {cell.smallSample && (
          <span className="rounded-full bg-coral-50 px-1.5 py-0.5 text-[10px] text-coral-600">小样本</span>
        )}
      </div>
      <div className="mt-0.5 text-[11px] text-slate-400 tabular-nums">
        {cell.sampleText} · {cell.intervalText}
      </div>
    </td>
  );
}

function SegmentCard({ view }: { view: EvaluationSegmentView }) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white/60 p-4">
      <div className="flex items-center justify-between">
        <h5 className="text-sm font-semibold text-slate-700">{view.label}</h5>
        <span className="text-[11px] text-slate-400">各态样本之和 = 该段全部板块事件</span>
      </div>
      <table className="mt-2 w-full border-collapse text-left">
        <thead>
          <tr className="text-[11px] text-slate-400">
            <th className="px-2 py-1 font-normal">状态</th>
            <th className="px-2 py-1 font-normal">10 日</th>
            <th className="px-2 py-1 font-normal">20 日</th>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((row) => (
            <tr key={row.state} className="border-t border-slate-100">
              <th scope="row" className="px-2 py-2 text-left">
                <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${STATE_TEXT_CLASS[row.state]}`}>
                  {row.label}
                </span>
              </th>
              {row.cells.map((cell) => (
                <HorizonCell key={cell.horizon} cell={cell} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SectorEvaluationPanel({
  evaluation,
}: {
  evaluation: SectorEvaluation | null | undefined;
}) {
  const views = useMemo(() => buildEvaluationViews(evaluation), [evaluation]);

  if (views.length === 0) {
    return (
      <div className="glass-card rounded-2xl p-6">
        <h4 className="text-base font-semibold text-slate-800">方向胜率验证</h4>
        <p className="mt-2 text-sm text-slate-500">评估尚未生成</p>
        <p className="mt-1 text-xs text-slate-400">
          重建板块产物后会按四态分别给出口径、样本量与 Wilson 区间。
        </p>
      </div>
    );
  }

  return (
    <div className="glass-card rounded-2xl p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-base font-semibold text-slate-800">方向胜率验证</h4>
          <p className="mt-1 text-xs text-slate-400">
            全部行业板块 · 按四态分组 · 未决事件不计入分母
          </p>
        </div>
        <span className="text-[11px] text-slate-400 tabular-nums">{evaluation?.algorithm_version}</span>
      </div>

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        {views.map((view) => (
          <SegmentCard key={view.segment} view={view} />
        ))}
      </div>

      <p className="mt-4 rounded-xl bg-slate-50 px-4 py-3 text-[11px] leading-relaxed text-slate-500">
        {evaluation?.note ?? DEFAULT_NOTE}
        <span className="mt-1 block text-slate-400">
          验证段为事后观察、不构成独立验证；四态样本之和等于该段全部板块事件，需要总体口径时可直接相加。
        </span>
      </p>
    </div>
  );
}
