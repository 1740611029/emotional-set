/** 强度解释面板：说明 -50~+50 有向强度、统一公式与两项构成。 */
import { useState } from 'react';
import type { SectorState, SectorStateBasis, SectorStrengthParts } from '../data/types';
import { STATE_LABELS, explainStrength } from '../lib/sectorStates';

export function StrengthExplainer({
  state,
  strength,
  parts,
  basis,
}: {
  state: SectorState | null;
  strength: number | null;
  parts: SectorStrengthParts | null;
  basis: SectorStateBasis | null;
}) {
  const [open, setOpen] = useState(false);
  const explanation = parts ? explainStrength(parts, basis) : null;
  const signedStrength = Math.max(-50, Math.min(50, strength ?? 0));
  const barWidth = Math.abs(signedStrength) / 2;

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="text-xs font-medium text-ocean-600 hover:text-ocean-700"
      >
        {open ? '收起强度说明' : '强度怎么读？'}
      </button>

      {open && (
        <div className="mt-2 rounded-xl border border-slate-100 bg-white/70 p-4 text-xs leading-relaxed text-slate-600">
          <p className="font-medium text-slate-700">有向强度表示价格结构相对中性的位置，不是涨跌预期</p>
          <p className="mt-1">
            取值 -50 ~ +50，0 为中性。正值偏强、负值偏弱；绝对值越大，表示偏离中性越远，不代表未来涨跌幅度。
          </p>

          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg bg-mint-50 px-3 py-2">
              <p className="font-medium text-mint-700">正值</p>
              <p className="text-mint-700/80">高于中性，相对偏强</p>
            </div>
            <div className="rounded-lg bg-coral-50 px-3 py-2">
              <p className="font-medium text-coral-600">负值</p>
              <p className="text-coral-600/80">低于中性，相对偏弱</p>
            </div>
          </div>

          <p className="mt-3 font-medium text-slate-700">构成（两项各占一半）</p>
          <ul className="mt-1 space-y-1">
            <li>
              <span className="text-slate-700">区间位置分</span>：收盘价在近 20 日高低区间中的百分位，
              0 = 区间最低，100 = 区间最高。
            </li>
            <li>
              <span className="text-slate-700">MA60 偏离分</span>：50 + 50·tanh(偏离 ÷ 10%)，
              偏离 +10% 约 88 分，−10% 约 12 分。
            </li>
            <li>
              所有状态使用同一公式：强度 = 两项均值 − 50；通道宽度为零时固定为 0。
            </li>
          </ul>

          {explanation && (
            <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2 font-mono text-[11px] text-slate-700">
              <div>
                区间位置 {parts?.range_position.toFixed(1)} · MA60 偏离 {parts?.ma_score.toFixed(1)}
              </div>
              <div className="mt-1">{explanation.formula}</div>
              <div className="mt-1 text-slate-500">
                当前状态 {state ? STATE_LABELS[state] : '—'} · {explanation.basisLabel}
              </div>
            </div>
          )}

          <div className="mt-3">
            <div className="relative h-2 w-full rounded-full bg-slate-100">
              <div
                className={`absolute top-0 h-2 rounded-full ${signedStrength >= 0 ? 'bg-mint-400' : 'bg-coral-400'}`}
                style={{
                  ...(signedStrength >= 0 ? { left: '50%' } : { right: '50%' }),
                  width: `${barWidth}%`,
                }}
              />
              <div className="absolute left-1/2 top-[-2px] h-3 w-px -translate-x-1/2 bg-slate-400" />
            </div>
            <div className="mt-1 flex justify-between text-[11px] text-slate-400">
              <span>-50 弱</span>
              <span>0 中性</span>
              <span>+50 强</span>
            </div>
          </div>

          <p className="mt-3 text-slate-500">
            注意：有向强度不是交易信号，正负与四态一起描述当前价格结构；绝对值越大只表示偏离中性越远。
          </p>
        </div>
      )}
    </div>
  );
}
