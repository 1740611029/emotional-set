import { Fragment, useMemo, useState } from 'react';
import { EmptyState, IndicatorHeader, Notice } from '../components/ui';
import { LhbIcon } from '../components/icons';
import { useLhbOverview } from '../hooks/useLhbOverview';
import type { LhbDailyRow, LhbOverviewTrader, LhbPosition, LhbRecord } from '../data/types';

type ViewKey = 'overview' | 'daily' | 'stocks';
type SortKey = 'net' | 'active' | 'records';

const yi = (value: number) => (value / 1e8).toFixed(2);
const signedYi = (value: number) => `${value > 0 ? '+' : ''}${yi(value)}亿`;

/** 离场标记保鲜期（交易日）：与管道 EXIT_MARK_DAYS 一致 */
const EXIT_MARK_DAYS = 3;

/** 展示层游资视图：在产物游资上附加「离场标记期内配对」索引（key=个股代码） */
type ViewTrader = LhbOverviewTrader & { exits: Map<string, LhbPosition> };

const EXIT_LABEL: Record<string, string> = {
  exited_confirmed: '已清仓',
  exited_probable: '疑似清仓',
  exited_forced: '已离场',
};

const EXIT_CLASS: Record<string, string> = {
  exited_confirmed: 'bg-coral-50 text-coral-600',
  exited_probable: 'bg-slate-100 text-slate-500',
  exited_forced: 'bg-slate-100 text-slate-400',
};

function ExitBadge({ status }: { status: LhbPosition['status'] }) {
  const label = EXIT_LABEL[status];
  if (!label) return null;
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap ${EXIT_CLASS[status] ?? 'bg-slate-100 text-slate-500'}`}>
      {label}
    </span>
  );
}

/**
 * 在场过滤：离场配对保留 exit_date 起 EXIT_MARK_DAYS 个交易日的标记展示，
 * 超过保鲜期整段隐藏（数据完整保留于产物，仅页面过滤）。
 * exit_date 不在交易日序列（异常）时保守显示，避免误隐藏。
 */
function buildViewTraders(
  traders: LhbOverviewTrader[], tradingDates: string[], asOf: string | null,
): ViewTrader[] {
  const refDate = asOf ?? tradingDates[tradingDates.length - 1] ?? null;
  const round2 = (value: number) => Math.round(value * 100) / 100;
  const result: ViewTrader[] = [];
  for (const trader of traders) {
    const exits = new Map<string, LhbPosition>();
    const records = trader.records.filter((record) => {
      const position = trader.positions?.find((item) => item.code === record.code);
      if (!position || position.status === 'active' || !position.exit_date) return true;
      const i = tradingDates.indexOf(position.exit_date);
      const j = refDate ? tradingDates.indexOf(refDate) : -1;
      if (i < 0 || j < 0 || j < i) return true;
      if (j - i > EXIT_MARK_DAYS) return false;
      exits.set(record.code, position);
      return true;
    });
    // 原有记录但全部配对已离场超期 → 整卡隐藏（游资级"不再记录"）
    if (trader.records.length > 0 && records.length === 0) continue;
    // 操作记录统一按日期倒序（新→旧），同日按净额绝对值倒序。
    // 显式排序不依赖产物顺序：旧版产物或回滚时，卡片取头部仍为最近操作、详情表格仍为倒序。
    records.sort((a, b) => b.date.localeCompare(a.date) || Math.abs(b.net) - Math.abs(a.net));
    // 统计口径与管道一致：只累计单日榜；展示什么就统计什么（离场隐藏同步剔除）
    const single = records.filter((record) => !record.is_multi_day);
    const stats = {
      active_days: new Set(single.map((record) => record.date)).size,
      records: single.length,
      stocks: new Set(single.map((record) => record.code)).size,
      buy_total: round2(single.reduce((sum, record) => sum + record.buy, 0)),
      sell_total: round2(single.reduce((sum, record) => sum + record.sell, 0)),
      net_total: round2(single.reduce((sum, record) => sum + record.net, 0)),
    };
    result.push({ ...trader, records, stats, exits });
  }
  return result;
}

const TIER_CLASS: Record<string, string> = {
  殿堂级: 'bg-brand-50 text-brand-700',
  极活跃: 'bg-ocean-50 text-ocean-700',
  新生代: 'bg-mint-50 text-mint-700',
  '牛散/机构化': 'bg-slate-100 text-slate-600',
};

const CONF_LABEL: Record<string, string> = { high: '高置信', medium: '中置信', low: '存疑' };
const CONF_CLASS: Record<string, string> = {
  high: 'bg-mint-50 text-mint-700',
  medium: 'bg-ocean-50 text-ocean-700',
  low: 'bg-coral-50 text-coral-600',
};

function ConfidenceBadge({ value }: { value: string }) {
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${CONF_CLASS[value] ?? 'bg-slate-100 text-slate-500'}`}>
      {CONF_LABEL[value] ?? value}
    </span>
  );
}

function NetText({ value, className = '' }: { value: number; className?: string }) {
  // A股习惯红买绿卖：净买入为正显示红（coral），净卖出为负显示绿（mint），
  // 与本页涨跌幅颜色语义（正红负绿）统一
  const tone = value > 0 ? 'text-coral-500' : value < 0 ? 'text-mint-600' : 'text-slate-500';
  return <span className={`${tone} tabular-nums ${className}`}>{signedYi(value)}</span>;
}

function RecentRecordRow({ record, exitStatus }: { record: LhbRecord; exitStatus?: LhbPosition['status'] | null }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-slate-400 tabular-nums">{record.date.slice(5)}</span>
      <span className="font-medium text-slate-700">{record.name}</span>
      {exitStatus && <ExitBadge status={exitStatus} />}
      {record.pct_chg !== null && (
        <span className={record.pct_chg > 0 ? 'text-coral-500' : 'text-mint-600'}>
          {record.pct_chg > 0 ? '+' : ''}{record.pct_chg.toFixed(2)}%
        </span>
      )}
      <NetText value={record.net} className="ml-auto" />
    </div>
  );
}

function TraderCard({ trader, onSelect }: { trader: ViewTrader; onSelect: () => void }) {
  const stats = trader.stats;
  // 卡片最近操作只展示单日榜，与统计口径一致（多日累计榜进详情页开关查看）
  const recentSingle = useMemo(
    () => trader.records.filter((record) => !record.is_multi_day).slice(0, 3),
    [trader.records],
  );
  return (
    <button
      type="button"
      onClick={onSelect}
      className="glass-card rounded-2xl p-5 text-left transition-shadow hover:shadow-lg hover:shadow-brand-100/60"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <h4 className="text-base font-bold text-slate-800">{trader.name}</h4>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${TIER_CLASS[trader.tier] ?? 'bg-slate-100 text-slate-500'}`}>
          {trader.tier}
        </span>
        <span className="ml-auto"><ConfidenceBadge value={trader.confidence} /></span>
      </div>
      <p className="text-xs text-slate-400 mb-3 line-clamp-1">{trader.style}</p>
      <div className="flex items-center gap-4 text-xs text-slate-500 mb-3">
        <span>活跃 <b className="text-slate-700">{stats.active_days}</b> 天</span>
        <span>操作 <b className="text-slate-700">{stats.records}</b> 笔</span>
        <span className="ml-auto">净买入 <NetText value={stats.net_total} className="font-bold" /></span>
      </div>
      <div className="space-y-1.5 border-t border-slate-100 pt-3">
        {recentSingle.map((record, index) => (
          <RecentRecordRow
            key={`${record.date}-${record.code}-${index}`}
            record={record}
            exitStatus={trader.exits.get(record.code)?.status}
          />
        ))}
        {recentSingle.length === 0 && <p className="text-xs text-slate-400">近一月无单日榜命中</p>}
      </div>
    </button>
  );
}

function TraderDetail({ trader, onBack }: { trader: ViewTrader; onBack: () => void }) {
  const [showMultiDay, setShowMultiDay] = useState(false);
  const records = useMemo(
    () => trader.records.filter((record) => showMultiDay || !record.is_multi_day),
    [trader.records, showMultiDay],
  );
  return (
    <div className="glass-card rounded-2xl p-6 flex flex-col h-full">
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <button type="button" onClick={onBack} className="text-sm text-brand-600 hover:text-brand-700">← 返回总览</button>
        <h3 className="text-xl font-bold text-slate-800">{trader.full_name}</h3>
        <span className={`rounded px-2 py-0.5 text-xs font-semibold ${TIER_CLASS[trader.tier] ?? 'bg-slate-100 text-slate-500'}`}>{trader.tier}</span>
      </div>
      <p className="text-xs text-slate-500 mb-4">{trader.style}{trader.note ? ` · ${trader.note}` : ''}</p>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
        {([
          ['活跃天数', `${trader.stats.active_days}`],
          ['操作笔数', `${trader.stats.records}`],
          ['买入合计', `${yi(trader.stats.buy_total)}亿`],
          ['卖出合计', `${yi(trader.stats.sell_total)}亿`],
          ['净买入', signedYi(trader.stats.net_total)],
        ] as const).map(([label, value]) => (
          <div key={label} className="rounded-xl bg-white/60 border border-slate-100 px-4 py-3">
            <p className="text-xs text-slate-400">{label}</p>
            <p className="text-base font-bold text-slate-700 tabular-nums">{value}</p>
          </div>
        ))}
      </div>

      <div className="mb-5">
        <h4 className="text-sm font-bold text-slate-700 mb-2">收录席位（{trader.seats.length}）</h4>
        <div className="flex flex-wrap gap-2">
          {trader.seats.map((seat) => (
            <span key={seat.full} className="inline-flex items-center gap-1.5 rounded-lg bg-white/70 border border-slate-100 px-2.5 py-1 text-xs text-slate-600">
              {seat.full}
              <ConfidenceBadge value={seat.confidence} />
            </span>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-3 mb-2">
        <h4 className="text-sm font-bold text-slate-700">操作记录（{records.length}）</h4>
        <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer">
          <input type="checkbox" checked={showMultiDay} onChange={(event) => setShowMultiDay(event.target.checked)} className="accent-brand-500" />
          显示多日累计榜（不计入统计）
        </label>
      </div>
      <div className="overflow-auto rounded-lg border border-slate-100 flex-1 min-h-0">
        <table className="w-full min-w-[860px]">
          <thead className="sticky top-0 bg-white z-10">
            <tr className="border-b border-slate-100">
              {['日期', '股票', '涨跌幅', '买入', '卖出', '净额', '板块', '席位', '置信'].map((label) => (
                <th key={label} className="text-left text-xs font-semibold text-slate-400 py-2.5 px-3 whitespace-nowrap">{label}</th>
              ))}
            </tr>
          </thead>
          <tbody className="text-sm">
            {records.map((record) => (
              <tr key={`${record.date}-${record.code}-${record.is_multi_day}`} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                <td className="py-2.5 px-3 text-slate-500 tabular-nums whitespace-nowrap">{record.date}</td>
                <td className="py-2.5 px-3 whitespace-nowrap">
                  <span className="font-medium text-slate-700">{record.name}</span>
                  <span className="text-xs text-slate-400 ml-1">{record.code}</span>
                  {record.is_multi_day && <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500">多日</span>}
                  {trader.exits.get(record.code) && <span className="ml-1.5"><ExitBadge status={trader.exits.get(record.code)!.status} /></span>}
                </td>
                <td className={`py-2.5 px-3 tabular-nums whitespace-nowrap ${(record.pct_chg ?? 0) > 0 ? 'text-coral-500' : (record.pct_chg ?? 0) < 0 ? 'text-mint-600' : 'text-slate-400'}`}>
                  {record.pct_chg === null ? '—' : `${record.pct_chg > 0 ? '+' : ''}${record.pct_chg.toFixed(2)}%`}
                </td>
                <td className="py-2.5 px-3 text-slate-600 tabular-nums">{yi(record.buy)}亿</td>
                <td className="py-2.5 px-3 text-slate-600 tabular-nums">{yi(record.sell)}亿</td>
                <td className="py-2.5 px-3"><NetText value={record.net} className="font-medium" /></td>
                <td className="py-2.5 px-3 text-slate-500 text-xs">{record.board ?? '—'}</td>
                <td className="py-2.5 px-3 text-xs text-slate-400 max-w-[220px] truncate" title={record.seat_hits.join('；')}>{record.seat_hits.join('；')}</td>
                <td className="py-2.5 px-3"><ConfidenceBadge value={record.confidence} /></td>
              </tr>
            ))}
            {records.length === 0 && (
              <tr><td colSpan={9}><EmptyState text="无匹配记录" /></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** 个股游资总览：单个游资在单只个股上的窗口内聚合（仅单日榜口径） */
interface StockTraderEntry {
  traderId: string;
  trader: string;
  tier: string;
  confidence: LhbRecord['confidence'];
  /** 窗口内单日榜买入/卖出/净额合计（龙虎榜披露的上榜日金额累计，非真实持仓） */
  buy: number;
  sell: number;
  net: number;
  /** 该游资在本股最近一次单日榜操作日期 */
  lastDate: string;
  /** 数据截至日（当天）的净额；当天无操作为 null */
  todayNet: number | null;
  /** （游资,个股）配对处于离场标记期内时的状态；null=在场 */
  exitStatus: LhbPosition['status'] | null;
}

/** 个股游资总览：单只个股聚合行 */
interface StockAgg {
  code: string;
  name: string;
  board: string | null;
  traders: StockTraderEntry[];
  buyTotal: number;
  sellTotal: number;
  netTotal: number;
  /** 该股最近一次单日榜操作日期（含任一名单游资） */
  lastDate: string;
  /** 当天（数据截至日）是否有名单游资操作 */
  hasToday: boolean;
  todayCount: number;
  todayNet: number;
}

/**
 * 个股游资总览聚合：把各游资的单日榜操作记录按个股归并。
 * 口径与页面统计一致——多日累计榜只存记录不参与任何金额统计；
 * 「当天」指数据截至日（as_of），龙虎榜为 T 日盘后披露。
 */
function aggregateStocks(traders: ViewTrader[], asOf: string | null): StockAgg[] {
  const byCode = new Map<string, StockAgg>();
  for (const trader of traders) {
    for (const record of trader.records) {
      if (record.is_multi_day) continue;
      let stock = byCode.get(record.code);
      if (!stock) {
        stock = {
          code: record.code, name: record.name, board: record.board,
          traders: [], buyTotal: 0, sellTotal: 0, netTotal: 0,
          lastDate: '', hasToday: false, todayCount: 0, todayNet: 0,
        };
        byCode.set(record.code, stock);
      }
      let entry = stock.traders.find((item) => item.traderId === trader.id);
      if (!entry) {
        entry = {
          traderId: trader.id, trader: trader.name, tier: trader.tier,
          confidence: record.confidence, buy: 0, sell: 0, net: 0,
          lastDate: '', todayNet: null, exitStatus: null,
        };
        stock.traders.push(entry);
      }
      const exit = trader.exits.get(record.code);
      if (exit) entry.exitStatus = exit.status;
      entry.buy += record.buy;
      entry.sell += record.sell;
      entry.net += record.net;
      if (record.date > entry.lastDate) entry.lastDate = record.date;
      if (asOf && record.date === asOf) {
        entry.todayNet = (entry.todayNet ?? 0) + record.net;
        stock.hasToday = true;
        stock.todayCount += 1;
        stock.todayNet += record.net;
      }
      stock.buyTotal += record.buy;
      stock.sellTotal += record.sell;
      stock.netTotal += record.net;
      if (record.date > stock.lastDate) stock.lastDate = record.date;
    }
  }
  for (const stock of byCode.values()) {
    stock.traders.sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
  }
  // 排序：当天有操作的排前 → 最近操作日期倒序 → 合计净额绝对值倒序
  return [...byCode.values()].sort((a, b) => {
    if (a.hasToday !== b.hasToday) return a.hasToday ? -1 : 1;
    if (a.lastDate !== b.lastDate) return a.lastDate > b.lastDate ? -1 : 1;
    return Math.abs(b.netTotal) - Math.abs(a.netTotal);
  });
}

function StockOverviewView({ traders, asOf }: { traders: ViewTrader[]; asOf: string | null }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const stocks = useMemo(() => aggregateStocks(traders, asOf), [traders, asOf]);
  const toggle = (code: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };
  return (
    <div className="glass-card rounded-2xl p-6 flex flex-col h-full">
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <h3 className="text-base font-bold text-slate-800">个股游资总览</h3>
        <span className="text-xs text-slate-400">
          共 {stocks.length} 只个股 · 当天指数据截至日 {asOf ?? '—'}
        </span>
      </div>
      <p className="text-xs text-slate-400 mb-4">
        口径：窗口内名单游资的单日榜操作按个股聚合；金额为龙虎榜披露的上榜日买卖金额累计，非真实持仓；
        多日累计榜不参与统计；点击行展开查看各游资明细。
      </p>
      <div className="overflow-auto rounded-lg border border-slate-100 flex-1 min-h-0">
        <table className="w-full min-w-[920px]">
          <thead className="sticky top-0 bg-white z-10">
            <tr className="border-b border-slate-100">
              {['股票', '板块', '在场游资', '合计净买入', '当天操作', '最近操作'].map((label) => (
                <th key={label} className="text-left text-xs font-semibold text-slate-400 py-2.5 px-3 whitespace-nowrap">{label}</th>
              ))}
            </tr>
          </thead>
          <tbody className="text-sm">
            {stocks.map((stock) => (
              <Fragment key={stock.code}>
                <tr
                  onClick={() => toggle(stock.code)}
                  className={`border-b border-slate-50 cursor-pointer transition-colors hover:bg-slate-50/50 ${expanded.has(stock.code) ? 'bg-brand-50/40' : ''}`}
                >
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    <span className="text-xs text-slate-300 mr-1.5">{expanded.has(stock.code) ? '▾' : '▸'}</span>
                    <span className="font-medium text-slate-700">{stock.name}</span>
                    <span className="text-xs text-slate-400 ml-1">{stock.code}</span>
                  </td>
                  <td className="py-2.5 px-3 text-slate-500 text-xs">{stock.board ?? '—'}</td>
                  <td className="py-2.5 px-3">
                    <div className="flex flex-wrap gap-1 max-w-[300px]">
                      {stock.traders.map((entry) => (
                        <span key={entry.traderId} className="inline-flex items-center gap-1 rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-medium text-brand-700 whitespace-nowrap">
                          {entry.trader}
                          {entry.exitStatus && <ExitBadge status={entry.exitStatus} />}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="py-2.5 px-3"><NetText value={stock.netTotal} className="font-semibold" /></td>
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    {stock.hasToday ? (
                      <span className="inline-flex items-center gap-1.5">
                        <span className="rounded bg-ocean-50 px-1.5 py-0.5 text-[10px] font-semibold text-ocean-700">
                          今日 {stock.todayCount} 家
                        </span>
                        <NetText value={stock.todayNet} className="font-semibold" />
                      </span>
                    ) : (
                      <span className="text-slate-300">—</span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-slate-500 tabular-nums whitespace-nowrap">{stock.lastDate}</td>
                </tr>
                {expanded.has(stock.code) && (
                  <tr key={`${stock.code}-detail`} className="border-b border-slate-100">
                    <td colSpan={6} className="bg-slate-50/60 px-6 py-3">
                      <table className="w-full">
                        <thead>
                          <tr>
                            {['游资', '层级', '置信', '买入合计', '卖出合计', '净额', '当天操作', '最近操作'].map((label) => (
                              <th key={label} className="text-left text-[11px] font-semibold text-slate-400 py-1.5 px-3 whitespace-nowrap">{label}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {stock.traders.map((entry) => (
                            <tr key={entry.traderId} className="border-t border-slate-100">
                              <td className="py-2 px-3 font-medium text-slate-700 whitespace-nowrap">
                                {entry.trader}
                                {entry.exitStatus && <span className="ml-1.5"><ExitBadge status={entry.exitStatus} /></span>}
                              </td>
                              <td className="py-2 px-3">
                                <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${TIER_CLASS[entry.tier] ?? 'bg-slate-100 text-slate-500'}`}>
                                  {entry.tier}
                                </span>
                              </td>
                              <td className="py-2 px-3"><ConfidenceBadge value={entry.confidence} /></td>
                              <td className="py-2 px-3 text-slate-600 tabular-nums">{yi(entry.buy)}亿</td>
                              <td className="py-2 px-3 text-slate-600 tabular-nums">{yi(entry.sell)}亿</td>
                              <td className="py-2 px-3"><NetText value={entry.net} className="font-medium" /></td>
                              <td className="py-2 px-3 whitespace-nowrap">
                                {entry.todayNet !== null
                                  ? <NetText value={entry.todayNet} className="font-semibold" />
                                  : <span className="text-slate-300">—</span>}
                              </td>
                              <td className="py-2 px-3 text-slate-500 tabular-nums whitespace-nowrap">{entry.lastDate}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {stocks.length === 0 && (
              <tr><td colSpan={6}><EmptyState text="窗口内暂无名单游资操作的个股" /></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DailyView({ dates, rowsFor }: { dates: string[]; rowsFor: (date: string) => LhbDailyRow[] }) {
  const [date, setDate] = useState(dates[dates.length - 1] ?? '');
  const rows = rowsFor(date);
  return (
    <div className="glass-card rounded-2xl p-6 flex flex-col h-full">
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <h3 className="text-base font-bold text-slate-800">每日动向</h3>
        <select
          value={date}
          onChange={(event) => setDate(event.target.value)}
          className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600"
        >
          {dates.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <span className="text-xs text-slate-400">共 {rows.length} 条操作（单日榜口径，按净额绝对值排序）</span>
      </div>
      <div className="overflow-auto rounded-lg border border-slate-100 flex-1 min-h-0">
        <table className="w-full min-w-[860px]">
          <thead className="sticky top-0 bg-white z-10">
            <tr className="border-b border-slate-100">
              {['游资', '层级', '股票', '涨跌幅', '买入', '卖出', '净额', '板块', '置信'].map((label) => (
                <th key={label} className="text-left text-xs font-semibold text-slate-400 py-2.5 px-3 whitespace-nowrap">{label}</th>
              ))}
            </tr>
          </thead>
          <tbody className="text-sm">
            {rows.map((row, index) => (
              <tr key={`${row.trader_id}-${row.code}-${index}`} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                <td className="py-2.5 px-3 font-medium text-slate-700 whitespace-nowrap">{row.trader}</td>
                <td className="py-2.5 px-3"><span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${TIER_CLASS[row.tier] ?? 'bg-slate-100 text-slate-500'}`}>{row.tier}</span></td>
                <td className="py-2.5 px-3 whitespace-nowrap">
                  <span className="font-medium text-slate-700">{row.name}</span>
                  <span className="text-xs text-slate-400 ml-1">{row.code}</span>
                  {row.exit_status && <span className="ml-1.5"><ExitBadge status={row.exit_status} /></span>}
                </td>
                <td className={`py-2.5 px-3 tabular-nums whitespace-nowrap ${(row.pct_chg ?? 0) > 0 ? 'text-coral-500' : (row.pct_chg ?? 0) < 0 ? 'text-mint-600' : 'text-slate-400'}`}>
                  {row.pct_chg === null ? '—' : `${row.pct_chg > 0 ? '+' : ''}${row.pct_chg.toFixed(2)}%`}
                </td>
                <td className="py-2.5 px-3 text-slate-600 tabular-nums">{yi(row.buy)}亿</td>
                <td className="py-2.5 px-3 text-slate-600 tabular-nums">{yi(row.sell)}亿</td>
                <td className="py-2.5 px-3"><NetText value={row.net} className="font-medium" /></td>
                <td className="py-2.5 px-3 text-slate-500 text-xs">{row.board ?? '—'}</td>
                <td className="py-2.5 px-3"><ConfidenceBadge value={row.confidence} /></td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={9}><EmptyState text="该日无已收录游资的操作" /></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function LhbTraders() {
  const { data, error, isLoading, refresh } = useLhbOverview();
  const [view, setView] = useState<ViewKey>('overview');
  const [sortKey, setSortKey] = useState<SortKey>('net');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // 在场过滤：离场超期配对隐藏、标记期配对保留+标记，stats 同口径重算
  const traders = useMemo(
    () => buildViewTraders(data?.traders ?? [], data?.trading_dates ?? [], data?.as_of ?? null),
    [data],
  );
  const sorted = useMemo(() => {
    const list = [...traders];
    if (sortKey === 'net') list.sort((a, b) => Math.abs(b.stats.net_total) - Math.abs(a.stats.net_total));
    if (sortKey === 'active') list.sort((a, b) => b.stats.active_days - a.stats.active_days);
    if (sortKey === 'records') list.sort((a, b) => b.stats.records - a.stats.records);
    return list;
  }, [traders, sortKey]);

  const selected = sorted.find((trader) => trader.id === selectedId) ?? null;
  const dailyRows = useMemo(() => {
    const map = new Map<string, LhbDailyRow[]>();
    for (const trader of traders) {
      for (const record of trader.records) {
        if (record.is_multi_day) continue;
        const list = map.get(record.date);
        const row = {
          trader: trader.name, trader_id: trader.id, tier: trader.tier,
          code: record.code, name: record.name,
          buy: record.buy, sell: record.sell, net: record.net,
          pct_chg: record.pct_chg, board: record.board, confidence: record.confidence,
          exit_status: trader.exits.get(record.code)?.status,
        };
        if (list) list.push(row);
        else map.set(record.date, [row]);
      }
    }
    for (const list of map.values()) list.sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
    return map;
  }, [traders]);

  const preparing = data !== null && data.data_state === 'preparing';

  return (
    <div className="flex h-full flex-col overflow-hidden p-8">
      <IndicatorHeader
        icon={LhbIcon}
        gradientClass="from-brand-400 to-mint-400"
        title="龙虎榜知名游资"
        subtitle="26 位游资每日龙虎榜操作追踪 · 席位来自公开资料整理 · 数据每日晚间自动更新"
        extra={(
          <div className="text-right">
            <p className="text-xs text-slate-400">数据截至 {data?.as_of ?? '—'}</p>
            <button type="button" onClick={refresh} className="text-xs text-brand-600 hover:text-brand-700">刷新</button>
          </div>
        )}
      />

      <div className="mb-5">
        <Notice>
          统计口径：仅计入单日榜金额（三日累计榜仅存记录不参与统计）；同股同席位跨上榜原因的重复披露已按当日去重。
          席位映射为公开资料交叉整理，<b>非官方认证</b>，低置信席位操作已标注「存疑」；数据仅供参考，不构成投资建议。
          在场判定：累计卖出覆盖买入、或大幅出货后该股再上榜未现身买方，标记「已清仓」；静默 4 个交易日且有观察窗口标记「疑似清仓」；标记保留 3 个交易日，之后该游资在该股的操作不再展示（判定仅基于窗口内数据，非真实持仓）。
        </Notice>
      </div>

      <div className="flex items-center gap-2 mb-5">
        {([['overview', '游资总览'], ['daily', '每日动向'], ['stocks', '个股游资总览']] as const).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setView(key)}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              view === key ? 'bg-brand-500 text-white shadow-md shadow-brand-200' : 'bg-white/70 text-slate-600 hover:bg-brand-50'
            }`}
          >
            {label}
          </button>
        ))}
        {view === 'overview' && !selected && (
          <div className="ml-auto flex items-center gap-2 text-xs text-slate-500">
            排序：
            {([['net', '净买额'], ['active', '活跃天数'], ['records', '操作笔数']] as const).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setSortKey(key)}
                className={`rounded px-2 py-1 ${sortKey === key ? 'bg-brand-50 text-brand-700 font-semibold' : 'hover:bg-slate-50'}`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      {isLoading && <div className="glass-card rounded-2xl p-12 text-center text-sm text-slate-500">龙虎榜游资数据加载中…</div>}
      {!isLoading && error && (
        <div className="glass-card rounded-2xl p-12 text-center">
          <p className="text-sm text-coral-500 mb-2">{error}</p>
          <button type="button" onClick={refresh} className="text-xs text-brand-600 hover:text-brand-700">重试</button>
        </div>
      )}
      {!isLoading && !error && preparing && (
        <div className="glass-card rounded-2xl p-12 text-center text-sm text-slate-500">
          龙虎榜游资数据准备中（首次建库或当日尚未更新），请稍后刷新
        </div>
      )}
      {!isLoading && !error && !preparing && view === 'overview' && selected && (
        <div className="flex-1 min-h-0">
          <TraderDetail trader={selected} onBack={() => setSelectedId(null)} />
        </div>
      )}
      {!isLoading && !error && !preparing && view === 'overview' && !selected && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 flex-1 min-h-0 overflow-y-auto">
          {sorted.map((trader) => (
            <TraderCard key={trader.id} trader={trader} onSelect={() => setSelectedId(trader.id)} />
          ))}
        </div>
      )}
      {!isLoading && !error && !preparing && view === 'daily' && (
        <div className="flex-1 min-h-0">
          <DailyView
            dates={data?.trading_dates ?? []}
            rowsFor={(date) => dailyRows.get(date) ?? []}
          />
        </div>
      )}
      {!isLoading && !error && !preparing && view === 'stocks' && (
        <div className="flex-1 min-h-0">
          <StockOverviewView traders={traders} asOf={data?.as_of ?? null} />
        </div>
      )}
    </div>
  );
}
