import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { InstitutionPositionChart, CHART_RANGES, type ChartRange } from '../components/InstitutionPositionChart';
import {
  InstitutionSummaryTable,
  RelationBadge,
  formatNextReturn,
  formatPosition,
  formatSigned,
  type InstitutionWindowStats,
} from '../components/InstitutionSummaryTable';
import { orderInstitutions, windowRelationStats } from '../lib/institutionView';
import { EmptyState, IndicatorHeader, Notice } from '../components/ui';
import { InstitutionIcon } from '../components/icons';
import { useInstitutionPositions } from '../hooks/useInstitutionPositions';
import {
  HISTORY_TABLE_HEIGHT_PX,
  hasMoreToLoad,
  historyProgressLabel,
  initialVisibleCount,
  nextVisibleCount,
  selectHistoryIndexes,
  shouldLoadMore,
} from '../lib/institutionHistory';
import type { InstitutionId, InstitutionRelation, InstitutionVariety } from '../data/types';

const VARIETY_TABS: { value: InstitutionVariety; label: string }[] = [
  { value: 'all', label: '四品种汇总' },
  { value: 'IF', label: 'IF 沪深300' },
  { value: 'IC', label: 'IC 中证500' },
  { value: 'IH', label: 'IH 上证50' },
  { value: 'IM', label: 'IM 中证1000' },
];

interface HistoryRow {
  date: string;
  long: number | null;
  short: number | null;
  net: number | null;
  chg: number | null;
  nextRet: number | null;
  relation: InstitutionRelation;
}

interface InstitutionHistoryGroup {
  id: InstitutionId;
  name: string;
  note: string;
  rows: HistoryRow[];
}

interface InstitutionHistoryTableProps {
  name: string;
  note: string;
  rows: HistoryRow[];
  varietyLabel: string;
  /** 筛选条件指纹（品种+日期区间），变化时重置已加载行数 */
  filterKey: string;
}

function InstitutionHistoryTable({ name, note, rows, varietyLabel, filterKey }: InstitutionHistoryTableProps) {
  const [visibleCount, setVisibleCount] = useState(() => initialVisibleCount(rows.length));
  const containerRef = useRef<HTMLDivElement>(null);
  const visibleRows = rows.slice(0, visibleCount);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    if (shouldLoadMore(el.scrollTop, el.scrollHeight, el.clientHeight, visibleCount, rows.length)) {
      setVisibleCount((prev) => nextVisibleCount(prev, rows.length));
    }
  }, [visibleCount, rows.length]);

  useEffect(() => {
    setVisibleCount(initialVisibleCount(rows.length));
  }, [filterKey, rows.length, rows[0]?.date]);

  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-2 mb-3">
        <h4 className="text-sm font-bold text-slate-700">{name}</h4>
        <span className="text-xs text-slate-400">{note}</span>
      </div>
      {rows.length === 0 ? (
        <div className="flex items-center justify-center" style={{ height: HISTORY_TABLE_HEIGHT_PX }}>
          <EmptyState text="当前筛选组合没有匹配记录" hint="请调整品种或日期区间" />
        </div>
      ) : (
        <>
          <div ref={containerRef} onScroll={handleScroll} className="overflow-auto rounded-lg" style={{ height: HISTORY_TABLE_HEIGHT_PX }}>
            <table className="w-full min-w-[900px]">
              <thead className="sticky top-0 bg-white z-10">
                <tr className="border-b border-slate-100">
                  {['日期', '品种', '多单（手）', '空单（手）', '净持仓（手）', '净持仓变化', '次日沪深300', '关系'].map((label) => (
                    <th key={label} className="text-left text-xs font-semibold text-slate-400 py-3 px-4 whitespace-nowrap">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="text-sm">
                {visibleRows.map((row) => (
                  <tr key={row.date} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                    <td className="py-3 px-4 text-slate-600">{row.date}</td>
                    <td className="py-3 px-4 text-slate-500">{varietyLabel}</td>
                    <td className="py-3 px-4 text-mint-600 tabular-nums">{formatPosition(row.long)}</td>
                    <td className="py-3 px-4 text-coral-500 tabular-nums">{formatPosition(row.short)}</td>
                    <td className="py-3 px-4 font-medium text-slate-700 tabular-nums">{formatPosition(row.net)}</td>
                    <td className="py-3 px-4 text-slate-600 tabular-nums">{formatSigned(row.chg)}</td>
                    <td className="py-3 px-4 text-slate-600 tabular-nums">{formatNextReturn(row.nextRet, row.relation)}</td>
                    <td className="py-3 px-4"><RelationBadge code={row.relation} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between mt-4 text-xs text-slate-500">
            <span>{historyProgressLabel(rows.length, visibleRows.length)}</span>
            {hasMoreToLoad(visibleCount, rows.length) && (
              <span className="text-slate-400">向下滚动加载更多</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export function InstitutionPositions() {
  const [variety, setVariety] = useState<InstitutionVariety>('all');
  const [chartInstitution, setChartInstitution] = useState<InstitutionId | 'all'>('all');
  const [range, setRange] = useState<ChartRange>(60);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const { data, error, isLoading, refresh } = useInstitutionPositions({ variety, limit: 120 });

  // 固定展示顺序：中信期货 → 国泰君安 → 其他机构（仅重排展示，不改动任何数值）
  const displayInstitutions = useMemo(
    () => (data && data.data_state !== 'preparing' ? orderInstitutions(data.institutions) : []),
    [data],
  );

  const historyGroups = useMemo<InstitutionHistoryGroup[]>(() => {
    if (!data || data.data_state === 'preparing') return [];
    return displayInstitutions.map((meta) => {
      const view = data.series[meta.id]?.[variety];
      const rows: HistoryRow[] = [];
      for (const index of selectHistoryIndexes(data.dates, { from, to }, Boolean(view))) {
        const date = data.dates[index];
        rows.push({
          date,
          long: view!.long[index] ?? null,
          short: view!.short[index] ?? null,
          net: view!.net[index] ?? null,
          chg: view!.chg[index] ?? null,
          nextRet: view!.next_ret[index] ?? null,
          relation: view!.relation[index] ?? 'N',
        });
      }
      return { id: meta.id, name: meta.name, note: meta.note, rows };
    });
  }, [displayInstitutions, variety, from, to]);

  // 当日快照的滑动窗口同向率：取当前品种 relation 尾部 10 / 20 个交易日，待判定不计入分母
  const windowStats = useMemo<InstitutionWindowStats>(() => {
    if (!data || data.data_state === 'preparing') return {};
    const result: InstitutionWindowStats = {};
    for (const meta of data.institutions) {
      const view = data.series[meta.id]?.[variety];
      if (!view) continue;
      result[meta.id] = {
        d10: windowRelationStats(view.relation, data.dates, 10),
        d20: windowRelationStats(view.relation, data.dates, 20),
      };
    }
    return result;
  }, [data, variety]);

  const selectedVariety = VARIETY_TABS.find((item) => item.value === variety) ?? VARIETY_TABS[0];
  const hasSeries = Boolean(data && data.data_state !== 'preparing' && data.dates.length > 0);

  return (
    <div className="p-8">
      <IndicatorHeader
        icon={InstitutionIcon}
        gradientClass="from-brand-400 to-coral-400"
        title="机构多空单"
        subtitle="中金所前 20 会员持仓排名与沪深300 次日走势的事后对照"
        extra={(
          <div className="text-right text-xs text-slate-400">
            <div>数据交易日：<span className="text-slate-600">{data?.as_of ?? '—'}</span></div>
            <div className="mt-1">最近更新：<span className="text-slate-600">{data?.generated_at?.replace('T', ' ') ?? '—'}</span></div>
            <button
              type="button"
              onClick={refresh}
              className="mt-2 px-3 py-1 rounded-lg bg-brand-50 text-brand-700 font-medium hover:bg-brand-100 transition-colors"
            >
              手动刷新
            </button>
          </div>
        )}
      />

      <div className="mb-5">
        <Notice>
          次日走势仅为事后统计对照，不构成预测或交易信号。数据为中金所前 20 会员持仓排名，非全市场持仓；
          多单/空单均为单边持仓量，单位为手。四品种汇总为手数直接相加，未按合约乘数折算，不可用于跨品种规模比较。
        </Notice>
      </div>
      {error && (
        <div className="mb-5">
          <Notice tone="warn">刷新失败：{error}。{data ? '当前保留最近一次成功数据。' : '暂无可展示数据，请重试。'}</Notice>
        </div>
      )}
      {data?.stale && data.data_state !== 'preparing' && (
        <div className="mb-5">
          <Notice tone="warn">数据陈旧：{data.stale_reason ?? '机构持仓尚未更新到最新交易日'}（最近数据日 {data.stale_since ?? data.as_of ?? '—'}），当前仍展示已落盘数据。</Notice>
        </div>
      )}

      {!data && isLoading && (
        <div className="glass-card rounded-2xl p-12 text-center text-sm text-slate-500">机构持仓加载中…</div>
      )}
      {!data && !isLoading && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState text="机构持仓数据暂不可用" hint="请检查数据服务后点击“手动刷新”重试" />
        </div>
      )}
      {data?.data_state === 'preparing' && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState text="机构持仓数据准备中" hint="产物生成后将在此展示，页面不会以占位数值替代" />
        </div>
      )}
      {data && data.data_state !== 'preparing' && !hasSeries && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState text="所选区间暂无机构持仓记录" hint="请调整筛选区间或稍后重试" />
        </div>
      )}

      {data && hasSeries && (
        <>
          <div className="glass-card rounded-2xl p-6 mb-8">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
              <div>
                <h3 className="text-lg font-bold text-slate-800">当日快照</h3>
                <p className="text-xs text-slate-400 mt-1">快照日期 {data.summary?.date ?? data.dates[data.dates.length - 1]}；待判定不计入同向率分母</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {VARIETY_TABS.map((tab) => (
                  <button
                    key={tab.value}
                    type="button"
                    onClick={() => setVariety(tab.value)}
                    className={`px-3 py-1.5 text-xs rounded-lg font-medium transition-colors ${variety === tab.value ? 'bg-brand-50 text-brand-700' : 'text-slate-500 hover:bg-slate-100'}`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="mb-4 text-xs text-slate-500 leading-relaxed">
              {variety === 'all'
                ? data.disclosure.aggregation_note
                : `当前为 ${selectedVariety.label} 分品种视图，持仓为单边手数；次日涨跌统一以沪深300 为事后对照。`}
            </div>
            <InstitutionSummaryTable
              institutions={displayInstitutions}
              summary={data.summary}
              stats={data.stats}
              windowStats={windowStats}
              variety={variety}
            />
          </div>

          <div className="glass-card rounded-2xl p-6 mb-8">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
              <div>
                <h3 className="text-lg font-bold text-slate-800">净持仓与大盘 走势对照</h3>
                <p className="text-xs text-slate-400 mt-1">左轴净持仓（手），右轴上证指数与沪深300 收盘价点位；图例可切换序列，缺失日期保持断点不插值</p>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <select
                  aria-label="图表机构筛选"
                  value={chartInstitution}
                  onChange={(event) => setChartInstitution(event.target.value as InstitutionId | 'all')}
                  className="px-3 py-1.5 text-xs rounded-lg bg-slate-50 text-slate-600 border border-slate-100"
                >
                  <option value="all">全部机构</option>
                  {displayInstitutions.map((meta) => <option key={meta.id} value={meta.id}>{meta.name}</option>)}
                </select>
                <div className="flex gap-1">
                  {CHART_RANGES.map((days) => (
                    <button
                      key={days}
                      type="button"
                      onClick={() => setRange(days)}
                      className={`px-3 py-1.5 text-xs rounded-lg font-medium ${range === days ? 'bg-brand-50 text-brand-700' : 'text-slate-500 hover:bg-slate-100'}`}
                    >
                      近{days}日
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <InstitutionPositionChart
              dates={data.dates}
              closes={data.benchmark.close}
              marketLines={data.market_lines}
              institutions={displayInstitutions}
              series={data.series}
              variety={variety}
              institution={chartInstitution}
              range={range}
              loading={isLoading}
            />
          </div>

          <div className="glass-card rounded-2xl p-6 mb-8">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
              <div>
                <h3 className="text-lg font-bold text-slate-800">历史明细</h3>
                <p className="text-xs text-slate-400 mt-1">按日期倒序；{selectedVariety.label}；滚动加载；按机构分开展示</p>
              </div>
              <div className="flex flex-wrap items-end gap-3 text-xs text-slate-500">
                <label className="flex flex-col gap-1">开始日期
                  <input type="date" value={from} onChange={(event) => setFrom(event.target.value)} className="px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-100" />
                </label>
                <label className="flex flex-col gap-1">结束日期
                  <input type="date" value={to} onChange={(event) => setTo(event.target.value)} className="px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-100" />
                </label>
              </div>
            </div>
              <div className="space-y-8 divide-y divide-slate-100">
              {historyGroups.map((group, groupIndex) => (
                <div key={group.id} className={groupIndex > 0 ? 'pt-8' : ''}>
                  <InstitutionHistoryTable
                    name={group.name}
                    note={group.note}
                    rows={group.rows}
                    varietyLabel={selectedVariety.label}
                    filterKey={`${variety}|${from}|${to}`}
                  />
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
