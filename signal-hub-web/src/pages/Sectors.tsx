/** 板块趋势页：同花顺行业指数的独立四态、走势与强度解释（单层清单，每个板块各自判定）。 */
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMarketData } from '../data/MarketDataProvider';
import { SECTOR_HISTORY_RANGES } from '../data/client';
import type { SectorHistoryRange } from '../data/client';
import { useSectorHistory } from '../data/useSectorHistory';
import type { SectorItem, SectorState } from '../data/types';
import { SectorStateBadge } from '../components/SectorStateBadge';
import { SectorEvaluationPanel } from '../components/SectorEvaluationPanel';
import { SectorTrendChart } from '../components/SectorTrendChart';
import { StrengthExplainer } from '../components/StrengthExplainer';
import { Notice } from '../components/ui';
import { SectorIcon } from '../components/icons';
import {
  STATE_LABELS,
  STATE_ORDER,
  STATE_TEXT_CLASS,
  formatDays,
  formatStrength,
  sortSectorItems,
} from '../lib/sectorStates';
import type { SectorSortKey } from '../lib/sectorStates';
import { nextSectorParams } from '../lib/sectorNavigation';
import type { SectorNavKey } from '../lib/sectorNavigation';

type StateFilter = SectorState | 'unclassified' | 'all';
type SortKey = SectorSortKey;

const FILTERS: { key: StateFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  ...STATE_ORDER.map((state) => ({ key: state as StateFilter, label: STATE_LABELS[state] })),
  { key: 'unclassified', label: '未分类' },
];

function Sparkline({ values, state }: { values: number[]; state: SectorState | null }) {
  if (values.length < 2) {
    return <span className="text-xs text-slate-300">—</span>;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 64;
      const y = 20 - ((value - min) / span) * 18;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const stroke = state === 'bull' || state === 'rise' ? '#14b8a6' : state ? '#f26522' : '#cbd5e1';
  return (
    <svg viewBox="0 0 64 22" className="w-16 h-5" aria-hidden="true">
      <polyline points={points} fill="none" stroke={stroke} strokeWidth="1.4" />
    </svg>
  );
}

function stateOf(item: SectorItem): SectorState | null {
  return item.state ?? null;
}

export function Sectors() {
  const { snapshot, error, isLoading } = useMarketData();
  const [params, setParams] = useSearchParams();
  const [range, setRange] = useState<SectorHistoryRange>(120);

  const block = snapshot?.sectors;
  // 板块只有收盘日线一条通道：与服务端判定的最近已完成交易日比较，落后即在页面显式提示
  const lagDays = block?.as_of ? (block.lag_days ?? 0) : 0;
  const sectors = useMemo(
    () => Object.values(block?.groups ?? {}).flat() as SectorItem[],
    [block],
  );

  const filter = (params.get('state') as StateFilter) || 'all';
  const sort = (params.get('sort') as SortKey) || 'strength';
  const query = params.get('q') ?? '';

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const rows = sectors.filter((item) => {
      const matchesState = filter === 'all'
        || (filter === 'unclassified' ? !stateOf(item) : stateOf(item) === filter);
      const matchesKeyword = !keyword
        || item.name.toLowerCase().includes(keyword)
        || item.code.toLowerCase().includes(keyword);
      return matchesState && matchesKeyword;
    });
    return sortSectorItems(rows, sort);
  }, [sectors, filter, sort, query]);

  const selectedCode = params.get('code');
  const selected = useMemo(
    () => sectors.find((item) => item.code === selectedCode) ?? null,
    [sectors, selectedCode],
  );

  // 未显式选中时默认强度最高的板块（filtered 已按强度降序）
  const active = selected ?? filtered[0] ?? null;

  // URL 里的 code 可能来自手改或失效链接：不在清单内时回落默认板块，并把回落结果写回地址栏，
  // 避免出现「URL 是一个板块、页面显示另一个板块」的分享链接
  useEffect(() => {
    if (!active || active.code === selectedCode) return;
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('code', active.code);
      return next;
    }, { replace: true });
  }, [active, selectedCode, setParams]);

  const history = useSectorHistory(active?.code ?? null, range);
  const ready = Boolean(history.code === active?.code && history.history);

  const update = (key: SectorNavKey, value: string) => {
    setParams((prev) => nextSectorParams(prev, key, value), { replace: true });
  };

  if (!snapshot && isLoading) {
    return (
      <div className="p-8">
        <div className="glass-card rounded-2xl p-10 text-center text-sm text-slate-500">板块数据加载中…</div>
      </div>
    );
  }

  if (sectors.length === 0) {
    return (
      <div className="p-8">
        <div className="glass-card rounded-2xl p-10 text-center">
          <p className="text-sm text-slate-500">暂无板块数据</p>
          <p className="mt-1 text-xs text-slate-400">
            {error ? `数据服务返回：${error}` : '等待数据服务完成首次板块计算'}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="flex items-center gap-4 mb-6">
        <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-brand-400 to-ocean-400 flex items-center justify-center text-white">
          <SectorIcon className="w-6 h-6" />
        </div>
        <div>
          <h3 className="text-2xl font-bold text-slate-800">板块趋势</h3>
          <p className="text-sm text-slate-500">
            同花顺行业指数 {sectors.length} 个行业，各自独立判定四态
          </p>
        </div>
        {block?.as_of && (
          <span className={`ml-auto text-xs tabular-nums ${lagDays > 0 ? 'text-coral-600 font-medium' : 'text-slate-400'}`}>
            数据截至 {block.as_of}
            {lagDays > 0 && `（滞后 ${lagDays} 个交易日）`}
          </span>
        )}
      </div>

      {block?.as_of && lagDays > 0 && (
        <div className="mb-4">
          <Notice tone="warn">
            板块数据滞后：板块日线截至 <span className="font-medium">{block.as_of}</span>，
            最近已完成交易日为 <span className="font-medium">{block.expected_as_of ?? '—'}</span>
            （差 {lagDays} 个交易日）。
            同花顺行业指数日线的发布通常晚于交易所，服务会在启动与收盘后自动补齐；
            若持续滞后，说明数据源尚未发布或拉取失败，当前展示的仍是旧交易日结论，请谨慎参考。
            {block.expected_basis === 'workday' && '（交易日历不可用，应到日期为工作日近似）'}
          </Notice>
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-xl bg-coral-50 px-4 py-2 text-xs text-coral-600">
          快照更新失败：{error}，当前展示最近一次成功数据
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[320px_1fr] gap-6 xl:relative">
        <aside className="glass-card rounded-2xl p-4 flex flex-col xl:absolute xl:inset-y-0 xl:left-0 xl:w-[320px]">
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => update('state', item.key === 'all' ? '' : item.key)}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                  filter === item.key ? 'bg-brand-500 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="mt-3 flex items-center gap-2">
            <input
              value={query}
              onChange={(event) => update('q', event.target.value)}
              placeholder="搜索板块名称或代码"
              className="flex-1 rounded-lg border border-slate-200 bg-white/70 px-3 py-1.5 text-xs text-slate-700 outline-none focus:border-brand-300"
            />
            <button
              type="button"
              onClick={() => update('sort', sort === 'strength' ? 'name' : 'strength')}
              className="rounded-lg bg-slate-100 px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-200"
            >
              {sort === 'strength' ? '强度↓' : '名称'}
            </button>
          </div>

          <p className="mt-2 text-[11px] text-slate-400">命中 {filtered.length} 个板块</p>

          <ul className="mt-3 max-h-[560px] xl:max-h-none xl:flex-1 xl:min-h-0 space-y-1 overflow-y-auto pr-1">
            {filtered.map((item) => {
              const state = stateOf(item);
              const isActive = active?.code === item.code;
              return (
                <li key={item.code}>
                  <button
                    type="button"
                    onClick={() => update('code', item.code)}
                    className={`w-full rounded-lg px-3 py-2 text-left transition-colors ${
                      isActive ? 'bg-brand-50 ring-1 ring-brand-200' : 'hover:bg-slate-50'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-slate-800 truncate">{item.name}</span>
                      <Sparkline values={item.spark} state={state} />
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded text-[11px] ${state ? STATE_TEXT_CLASS[state] : 'bg-slate-100 text-slate-500'}`}>
                        {state ? STATE_LABELS[state] : '数据不足'}
                      </span>
                      <span className="text-[11px] text-slate-500 tabular-nums">强度 {formatStrength(item.strength)}</span>
                      <span className="text-[11px] text-slate-400 tabular-nums">{formatDays(item.days)}</span>
                    </div>
                  </button>
                </li>
              );
            })}
            {filtered.length === 0 && (
              <li className="py-8 text-center text-xs text-slate-400">无匹配板块</li>
            )}
          </ul>
        </aside>

        <section className="space-y-4 xl:col-start-2">
          {!active && (
            <div className="glass-card rounded-2xl p-10 text-center text-sm text-slate-500">
              请选择一个板块
            </div>
          )}

          {active && (
            <div className="glass-card rounded-2xl p-6">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h4 className="text-xl font-bold text-slate-800">{active.name}</h4>
                    <span className="text-xs text-slate-400 tabular-nums">{active.code}</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-400">
                    同花顺行业指数{active.date ? ` · 数据日 ${active.date}` : ''}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <SectorStateBadge state={stateOf(active)} />
                  {active.data_lag && (
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">数据滞后</span>
                  )}
                  {active.data_anomaly && (
                    <span className="rounded-full bg-coral-50 px-2 py-0.5 text-[11px] text-coral-600">数据异常</span>
                  )}
                </div>
              </div>

              <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                  <p className="text-[11px] text-slate-400">方向强度</p>
                  <p className="text-lg font-bold text-slate-800 tabular-nums">{formatStrength(active.strength)}</p>
                </div>
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                  <p className="text-[11px] text-slate-400">强度口径</p>
                  <p className="text-sm font-medium text-slate-700">
                    {active.strength_basis === 'neutral' ? '中性（固定 0）'
                      : active.strength_basis === 'directional' ? '有向（统一公式）' : '—'}
                  </p>
                </div>
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                  <p className="text-[11px] text-slate-400">状态持续</p>
                  <p className="text-lg font-bold text-slate-800 tabular-nums">{formatDays(active.days)}</p>
                </div>
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                  <p className="text-[11px] text-slate-400">数据日</p>
                  <p className="text-sm font-medium text-slate-700 tabular-nums">{active.date ?? '—'}</p>
                </div>
              </div>

              <StrengthExplainer
                state={stateOf(active)}
                strength={active.strength}
                parts={active.strength_parts}
                basis={active.strength_basis}
              />

              <div className="mt-4 flex items-center justify-between">
                <div className="flex gap-1.5">
                  {SECTOR_HISTORY_RANGES.map((option) => (
                    <button
                      key={option}
                      type="button"
                      onClick={() => setRange(option)}
                      className={`px-2.5 py-1 rounded-full text-xs ${
                        range === option ? 'bg-ocean-500 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                      }`}
                    >
                      {option} 日
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-2 text-[11px] text-slate-400">
                  {STATE_ORDER.map((state) => (
                    <span key={state} className="inline-flex items-center gap-1">
                      <span className={`inline-block w-2 h-2 rounded-full ${STATE_TEXT_CLASS[state].split(' ')[0]}`} />
                      {STATE_LABELS[state]}
                    </span>
                  ))}
                </div>
              </div>

              <div className="mt-2">
                <SectorTrendChart
                  dates={ready ? history.history!.series.map((row) => row.date) : []}
                  closes={ready ? history.history!.series.map((row) => row.close) : []}
                  states={ready ? history.history!.series.map((row) => row.state) : []}
                  loading={history.isLoading || !ready}
                />
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
                {ready && history.history!.coverage.start && (
                  <span>
                    覆盖 {history.history!.coverage.start} ~ {history.history!.coverage.end}（{history.history!.coverage.points} 个交易日）
                  </span>
                )}
                {ready && history.history!.insufficient && <span>历史长度不足 {range} 日，已按实际长度绘制</span>}
                {ready && block?.as_of && history.history!.as_of && block.as_of !== history.history!.as_of && (
                  <span className="text-coral-500">走势数据截至 {history.history!.as_of}，与快照 {block.as_of} 不一致</span>
                )}
                {history.error && <span className="text-coral-500">走势加载失败：{history.error}</span>}
              </div>
            </div>
          )}

          <div className="rounded-xl bg-slate-50 px-4 py-3 text-[11px] leading-relaxed text-slate-500">
            板块趋势为描述性分类：四态与强度反映当前价格结构，方向胜率不代表交易收益。
            每个板块都按自身日线独立判定；任何状态都不构成买卖建议。
          </div>
        </section>
      </div>

      {/* 口径为全部行业板块，与选中板块无关，因此放在列表/详情网格之外 */}
      <div className="mt-6">
        <SectorEvaluationPanel evaluation={block?.evaluation} />
      </div>
    </div>
  );
}
