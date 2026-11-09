import type { Snapshot } from '../data/types';
import { ChangeText, EmptyState, SignalBadge } from './ui';

type Row = Snapshot['recent_signals'][number];

export function SignalTable({
  rows,
  asOf,
  provisional,
  limit,
  showIndex = true,
}: {
  rows: Row[];
  asOf: string | null;
  provisional: boolean;
  limit?: number;
  showIndex?: boolean;
}) {
  if (rows.length === 0) {
    return (
      <EmptyState
        text="最近没有已确认信号"
        hint="确认型信号需经价格、动量、量能三层确认后才会出现在这里"
      />
    );
  }
  const visible = limit ? rows.slice(0, limit) : rows;
  const headers = [
    '日期',
    ...(showIndex ? ['指数'] : []),
    '信号类型',
    '等级',
    '触发价',
    '近 20 日回撤',
    '至今涨跌幅',
    '状态',
  ];
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-slate-100">
            {headers.map((header) => (
              <th
                key={header}
                className="text-left text-xs font-semibold text-slate-400 uppercase tracking-wider py-3 px-4"
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="text-sm">
          {visible.map((row, index) => {
            const isProvisional = provisional && row.date === asOf;
            return (
              <tr
                key={`${row.code}-${row.date}-${row.type}`}
                className={`${index === visible.length - 1 ? '' : 'border-b border-slate-50'} hover:bg-slate-50/50 transition-colors`}
              >
                <td className="py-3 px-4 text-slate-600 tabular-nums">{row.date}</td>
                {showIndex && <td className="py-3 px-4 font-medium text-slate-800">{row.name}</td>}
                <td className="py-3 px-4">
                  <SignalBadge type={row.type} size="sm" />
                </td>
                <td className="py-3 px-4">
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      row.grade === 'A'
                        ? 'bg-brand-100 text-brand-700'
                        : 'bg-slate-100 text-slate-500'
                    }`}
                  >
                    {row.grade ?? '--'}
                  </span>
                </td>
                <td className="py-3 px-4 text-slate-600 tabular-nums">
                  {row.price.toLocaleString('zh-CN', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </td>
                <td className="py-3 px-4 tabular-nums text-coral-500">
                  {row.max_drawdown_20 === null || row.max_drawdown_20 === undefined
                    ? '--'
                    : `${(row.max_drawdown_20 * 100).toFixed(1)}%`}
                </td>
                <td className="py-3 px-4 tabular-nums">
                  <ChangeText value={row.change_since} />
                </td>
                <td className="py-3 px-4">
                  {isProvisional ? (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-ocean-50 text-ocean-700 font-medium">
                      盘中临时
                    </span>
                  ) : (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-mint-50 text-mint-700 font-medium">
                      已确认
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
