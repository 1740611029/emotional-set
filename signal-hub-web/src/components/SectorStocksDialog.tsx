/** 【板块趋势】页个股弹窗：全部成分股列表，点击列头按 涨停时间/连板数/封单金额/成交额/涨幅 排序。
 * 非涨停股（limit 为 null）涨停三列显示 — 并置底；涨停时间越早封板越强（09:25 一字板最强）。
 * 颜色遵循项目色彩系统（珊瑚橙上涨 / 薄荷绿下跌），与 ui.tsx ChangeText 一致。 */
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { SectorStock } from '../data/types';
import type { SectorStocksValue } from '../data/useSectorStocks';
import {
  defaultDirection,
  nextDirection,
  sortSectorStocks,
} from '../lib/sectorStocks';
import type {
  SectorStockSortDirection,
  SectorStockSortKey,
} from '../lib/sectorStocks';

const SORT_COLUMNS: { key: SectorStockSortKey; label: string; hint: string }[] = [
  { key: 'first_seal', label: '涨停时间', hint: '越早封板越强（09:25 一字板最强）' },
  { key: 'boards', label: '连板数', hint: '连板数越高越强' },
  { key: 'seal_amount', label: '封单金额', hint: '封单金额单位：万元' },
  { key: 'amount', label: '成交额', hint: '成交额（元），非涨停股也可排序' },
  { key: 'change_pct', label: '涨幅', hint: '涨跌幅 %' },
];

function SortHeader({
  label,
  hint,
  active,
  direction,
  onClick,
}: {
  label: string;
  hint: string;
  active: boolean;
  direction: SectorStockSortDirection;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={hint}
      onClick={onClick}
      className={`inline-flex items-center gap-1 transition-colors ${
        active ? 'font-semibold text-brand-600' : 'font-medium text-slate-400 hover:text-brand-600'
      }`}
    >
      {label}
      <span className="text-[9px]">{active ? (direction === 'desc' ? '▼' : '▲') : '↕'}</span>
    </button>
  );
}

function pctCell(value: number | null): ReactNode {
  if (value == null) return <span className="text-slate-300">—</span>;
  const tone = value > 0 ? 'text-coral-500' : value < 0 ? 'text-mint-600' : 'text-slate-500';
  return <span className={`tabular-nums ${tone}`}>{(value > 0 ? '+' : '') + value.toFixed(2)}%</span>;
}

function textCell(value: number | null, digits = 2): ReactNode {
  if (value == null) return <span className="text-slate-300">—</span>;
  return <span className="tabular-nums text-slate-600">{value.toFixed(digits)}</span>;
}

function sealCell(value: number | null): ReactNode {
  if (value == null) return <span className="text-slate-300">—</span>;
  return (
    <span className="tabular-nums text-slate-700 font-medium">
      {value.toLocaleString('zh-CN')}万
    </span>
  );
}

function boardsCell(value: number | null): ReactNode {
  if (value == null) return <span className="text-slate-300">—</span>;
  const strong = value >= 3;
  return (
    <span
      className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-semibold tabular-nums ${
        strong ? 'bg-brand-50 text-brand-600' : 'bg-slate-100 text-slate-600'
      }`}
    >
      {value} 板
    </span>
  );
}

export function SectorStocksDialog({
  code,
  value,
  onClose,
}: {
  code: string | null;
  value: SectorStocksValue;
  onClose: () => void;
}) {
  const [sortKey, setSortKey] = useState<SectorStockSortKey>('first_seal');
  const [direction, setDirection] = useState<SectorStockSortDirection>(defaultDirection('first_seal'));

  const matched = code !== null && value.code === code;
  const ready = matched && value.data?.data_state === 'ready';
  const preparing = matched && value.data?.data_state === 'preparing';
  const stocks: SectorStock[] = ready ? (value.data?.stocks ?? []) : [];
  const sorted = useMemo(
    () => sortSectorStocks(stocks, sortKey, direction),
    [stocks, sortKey, direction],
  );

  const handleSort = (key: SectorStockSortKey) => {
    if (key === sortKey) setDirection(nextDirection(direction));
    else {
      setSortKey(key);
      setDirection(defaultDirection(key));
    }
  };

  const limitUpCount = useMemo(() => stocks.filter((stock) => stock.limit).length, [stocks]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}
    >
      <div
        className="relative flex flex-col w-full max-w-5xl h-[90vh] rounded-2xl bg-white shadow-xl overflow-hidden"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div>
            <h4 className="text-lg font-bold text-slate-800">
              {value.data?.name ?? '板块个股'}
              <span className="ml-2 text-xs font-normal text-slate-400 tabular-nums">{code}</span>
            </h4>
            <p className="mt-0.5 text-[11px] text-slate-400">
              全部成分股 · 点击列头切换排序（默认按涨停时间：越早封板越强）
            </p>
            {ready && value.data && (
              <p className="mt-0.5 text-[11px] text-slate-400 tabular-nums">
                共 {value.data.total} 只
                {value.data.limit_as_of ? ` · 涨停池数据日 ${value.data.limit_as_of}（东财股池）` : ''}
                {value.data.as_of ? ` · 成分股数据日 ${value.data.as_of}（同花顺）` : ''}
                {limitUpCount > 0 ? ` · 其中涨停 ${limitUpCount} 只` : ''}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            aria-label="关闭弹窗"
          >
            <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">
          {!matched && value.isLoading && (
            <p className="text-xs text-slate-400">成分股加载中…</p>
          )}
          {!matched && !value.isLoading && value.error && (
            <p className="text-xs text-coral-600">成分股加载失败：{value.error}</p>
          )}
          {preparing && (
            <p className="text-xs text-slate-400">
              成分股快照准备中（请先运行 pipeline/fetch_sector_stocks.py 生成数据）
            </p>
          )}
          {ready && sorted.length === 0 && (
            <p className="text-xs text-slate-400">该板块暂无成分股数据</p>
          )}

          {ready && sorted.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-100 text-[11px] text-slate-400">
                    <th className="py-2 pr-3 font-medium whitespace-nowrap">代码</th>
                    <th className="py-2 pr-3 font-medium whitespace-nowrap">名称</th>
                    <th className="py-2 pr-3 font-medium whitespace-nowrap text-right">现价</th>
                    {SORT_COLUMNS.map(({ key, label, hint }) => (
                      <th key={key} className="py-2 pr-3 whitespace-nowrap">
                        <SortHeader
                          label={label}
                          hint={hint}
                          active={sortKey === key}
                          direction={direction}
                          onClick={() => handleSort(key)}
                        />
                      </th>
                    ))}
                    <th className="py-2 pr-3 font-medium whitespace-nowrap text-right">涨速%</th>
                    <th className="py-2 pr-3 font-medium whitespace-nowrap text-right">换手%</th>
                    <th className="py-2 pr-3 font-medium whitespace-nowrap text-right">量比</th>
                    <th className="py-2 pr-3 font-medium whitespace-nowrap text-right">流通市值</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {sorted.map((stock) => {
                    const limit = stock.limit;
                    return (
                      <tr
                        key={stock.code}
                        className={`${limit ? 'bg-coral-50/40' : 'hover:bg-slate-50/60'} hover:bg-slate-50/60`}
                      >
                        <td className="py-1.5 pr-3 font-mono text-[11px] text-slate-500 whitespace-nowrap">{stock.code}</td>
                        <td className="py-1.5 pr-3 font-medium text-slate-700 whitespace-nowrap">
                          {stock.name}
                          {limit?.status && (
                            <span
                              className="ml-1 rounded bg-brand-50 px-1 py-0.5 text-[10px] font-normal text-brand-600"
                              title="封板状态"
                            >
                              {limit.status}
                            </span>
                          )}
                        </td>
                        <td className="py-1.5 pr-3 text-right whitespace-nowrap">{textCell(stock.price)}</td>
                        <td className="py-1.5 pr-3 whitespace-nowrap">
                          {limit?.first_seal != null
                            ? <span className="tabular-nums text-slate-700 font-medium">{limit.first_seal}</span>
                            : <span className="text-slate-300">—</span>}
                        </td>
                        <td className="py-1.5 pr-3 whitespace-nowrap">{boardsCell(limit?.boards ?? null)}</td>
                        <td className="py-1.5 pr-3 whitespace-nowrap">{sealCell(limit?.seal_amount ?? null)}</td>
                        <td className="py-1.5 pr-3 text-right whitespace-nowrap">
                          {stock.amount_text
                            ? <span className="tabular-nums text-slate-600">{stock.amount_text}</span>
                            : <span className="text-slate-300">—</span>}
                        </td>
                        <td className="py-1.5 pr-3 text-right whitespace-nowrap">{pctCell(stock.change_pct)}</td>
                        <td className="py-1.5 pr-3 text-right whitespace-nowrap">{pctCell(stock.speed_pct)}</td>
                        <td className="py-1.5 pr-3 text-right whitespace-nowrap">{pctCell(stock.turnover_pct)}</td>
                        <td className="py-1.5 pr-3 text-right whitespace-nowrap">{textCell(stock.volume_ratio)}</td>
                        <td className="py-1.5 pr-3 text-right whitespace-nowrap">
                          {stock.float_mv_text
                            ? <span className="tabular-nums text-slate-600">{stock.float_mv_text}</span>
                            : <span className="text-slate-300">—</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="border-t border-slate-100 px-5 py-2.5 text-[11px] leading-relaxed text-slate-400">
          排序规则：涨停时间越早封板越强（09:25 一字板最强）；非涨停股涨停三列（涨停时间/连板数/封单金额）显示 — 并置底；
          封单金额单位万元；连板数 ≥3 高亮。数据源：成分股 = 同花顺行业成分股快照（实时行情页，收盘后保留当日数值），
          涨停池 = 东财涨停股池；两者数据日可能不同，以上方标识为准。超过 100 只的板块仅含同花顺页面当日涨幅前 100 只。
          涨跌色为本项目口径（珊瑚橙涨 / 薄荷绿跌），任何数据不构成买卖建议。
        </div>
      </div>
    </div>
  );
}