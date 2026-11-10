import type {
  InstitutionMeta,
  InstitutionRelation,
  InstitutionStat,
  InstitutionSummaryRow,
  InstitutionVariety,
} from '../data/types';

export const RELATION_META: Record<InstitutionRelation, { label: string; className: string }> = {
  S: { label: '同向', className: 'bg-mint-50 text-mint-700' },
  H: { label: '对冲', className: 'bg-coral-50 text-coral-600' },
  P: { label: '待判定', className: 'bg-slate-100 text-slate-500' },
  N: { label: '无数据', className: 'bg-slate-100 text-slate-400' },
};

export function RelationBadge({ code }: { code: InstitutionRelation }) {
  const meta = RELATION_META[code];
  return (
    <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${meta.className}`}>
      {meta.label}
    </span>
  );
}

export function formatPosition(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toLocaleString();
}

export function formatSigned(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${value > 0 ? '+' : ''}${value.toLocaleString()}`;
}

export function formatNextReturn(value: number | null | undefined, relation: InstitutionRelation): string {
  if (value === null || value === undefined) return relation === 'N' ? '无数据' : '待判定';
  return `${value > 0 ? '+' : ''}${(value * 100).toFixed(2)}%`;
}

/** 当日快照用的滑动窗口同向率：近 10 / 近 20 个交易日。 */
export type InstitutionWindowStats = Partial<
  Record<InstitutionMeta['id'], { d10: InstitutionStat; d20: InstitutionStat }>
>;

interface Props {
  institutions: InstitutionMeta[];
  summary: { date: string; institutions: InstitutionSummaryRow[] } | null;
  stats: Partial<Record<InstitutionMeta['id'], Partial<Record<InstitutionVariety, InstitutionStat>>>>;
  windowStats?: InstitutionWindowStats;
  variety: InstitutionVariety;
}

/** 滑动窗口同向率单元格：主数值 + 已判定样本量，悬停给出同向/对冲/待判定与已判定区间。 */
function WindowRateCell({ stat, label }: { stat: InstitutionStat | undefined; label: string }) {
  if (!stat) return <span className="text-slate-400">无数据</span>;
  const title = stat.rate === null
    ? `${label}：窗口内暂无已判定样本（待判定 ${stat.pending} 不计入分母）`
    : `${label}：已判定 ${stat.decided} 个交易日（同向 ${stat.same} / 对冲 ${stat.hedge}），待判定 ${stat.pending} 不计入分母；区间 ${stat.start ?? '—'} 至 ${stat.end ?? '—'}`;
  return (
    <div className="flex flex-col gap-0.5" title={title}>
      <span className="text-base font-bold text-ocean-600 tabular-nums">
        {stat.rate === null ? '—' : `${(stat.rate * 100).toFixed(1)}%`}
      </span>
      <span className="text-[11px] text-slate-400">
        n={stat.decided}{stat.decided > 0 ? ` · 同${stat.same}/对${stat.hedge}` : ''}
      </span>
    </div>
  );
}

export function InstitutionSummaryTable({ institutions, summary, stats, windowStats, variety }: Props) {
  const rows = summary?.institutions ?? [];
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1400px]">
        <thead>
          <tr className="border-b border-slate-100">
            {['机构', '多单（手）', '空单（手）', '净持仓（手）', '较上一交易日', '次日沪深300', '关系', '同向率（含样本量）', '近10个交易日同向率', '近20个交易日同向率'].map((label) => (
              <th key={label} className="text-left text-xs font-semibold text-slate-400 py-3 px-4 whitespace-nowrap">{label}</th>
            ))}
          </tr>
        </thead>
        <tbody className="text-sm">
          {institutions.map((meta) => {
            const row = rows.find((item) => item.id === meta.id);
            const stat = stats[meta.id]?.[variety];
            const relation = row?.relation ?? 'N';
            return (
              <tr key={meta.id} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                <td className="py-4 px-4">
                  <div className="font-medium text-slate-700">{meta.name}</div>
                  <div className="text-xs text-slate-400 mt-1 max-w-44" title={meta.note}>{meta.note}</div>
                </td>
                <td className="py-4 px-4 text-mint-600 tabular-nums">{formatPosition(row?.long)}</td>
                <td className="py-4 px-4 text-coral-500 tabular-nums">{formatPosition(row?.short)}</td>
                <td className="py-4 px-4 font-semibold text-slate-700 tabular-nums">{formatPosition(row?.net)}</td>
                <td className="py-4 px-4 text-slate-600 tabular-nums">{formatSigned(row?.chg)}</td>
                <td className="py-4 px-4 text-slate-600 tabular-nums">{formatNextReturn(row?.next_ret, relation)}</td>
                <td className="py-4 px-4"><RelationBadge code={relation} /></td>
                <td className="py-4 px-4">
                  {stat ? (
                    <div className="text-xs leading-relaxed">
                      <div className="flex items-center gap-2">
                        <span className="text-base font-bold text-ocean-600">
                          {stat.rate === null ? '—' : `${(stat.rate * 100).toFixed(1)}%`}
                        </span>
                        <span className="text-slate-500">已判定 n={stat.decided}</span>
                        {stat.small_sample && (
                          <span className="px-2 py-0.5 rounded-full bg-coral-50 text-coral-600">小样本</span>
                        )}
                      </div>
                      <div className="text-slate-400 mt-1">
                        同向 {stat.same} / 对冲 {stat.hedge} / 待判定 {stat.pending}；区间 {stat.start ?? '—'} 至 {stat.end ?? '—'}
                      </div>
                    </div>
                  ) : <span className="text-slate-400">无数据</span>}
                </td>
                <td className="py-4 px-4">
                  <WindowRateCell stat={windowStats?.[meta.id]?.d10} label="近10个交易日" />
                </td>
                <td className="py-4 px-4">
                  <WindowRateCell stat={windowStats?.[meta.id]?.d20} label="近20个交易日" />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
