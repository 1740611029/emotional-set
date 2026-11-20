/** 前端各页数据契约类型。指数总览/板块趋势页已于 2026-11-20 下线，相关类型一并移除。 */

export type DataState = 'ready' | 'preparing' | 'degraded';

/** 单只 ETF 在齐备区间内的成交额序列（与 EtfVolumeSeries.dates 等长，单位元）。 */
export interface EtfVolumeSeriesEntry {
  code: string;
  name: string;
  /** 跟踪指数名，图例展示用 */
  index: string;
  amount: number[];
}

/** 【ETF 放量】页专用序列：列式结构，成交额以元为单位，指数缺失为 null。 */
export interface EtfVolumeSeries {
  data_state: DataState;
  as_of: string | null;
  generated_at: string | null;
  stale: boolean;
  stale_reason: string | null;
  coverage: { start: string | null; end: string | null; trading_days: number | null };
  dates: string[];
  etfs: EtfVolumeSeriesEntry[];
  total_amount: number[];
  index_close: (number | null)[];
  /** 全池爆量日（L3），取自生产放量事件产物 */
  l3_dates: string[];
}

/** 宽基 ETF 申购净流入条目：净流入 = Σ(份额变化×单位净值)，只读展示层。 */
export interface EtfShareFlowUniverseEntry {
  code: string;
  name: string;
  /** 跟踪指数名（展示用，可能为空） */
  index: string;
}

/** 【宽基 ETF 申购净流入】列式序列：净流入单位亿元，指数缺失为 null。 */
export interface EtfShareFlow {
  data_state: DataState;
  as_of: string | null;
  generated_at: string | null;
  stale: boolean;
  stale_reason: string | null;
  coverage: {
    start: string | null; end: string | null; trading_days: number;
    etf_count: number; min_counted: number; max_counted: number;
  };
  dates: string[];
  net_inflow_yi: number[];
  index_close: (number | null)[];
  stats: { last_day_yi: number | null; last_60d_yi: number; last_60d_days: number };
  universe: EtfShareFlowUniverseEntry[];
  sources: { share: string; nav: string };
}

/** 机构多空单：次日关系仅用于事后统计，不参与信号计算。 */
export type InstitutionId = 'guotai' | 'citic' | 'others';
export type InstitutionVariety = 'all' | 'IF' | 'IC' | 'IH' | 'IM';
export type InstitutionRelation = 'S' | 'H' | 'P' | 'N';

export interface InstitutionMeta {
  id: InstitutionId;
  name: string;
  note: string;
}

export interface InstitutionSeriesView {
  long: (number | null)[];
  short: (number | null)[];
  net: (number | null)[];
  chg: (number | null)[];
  next_ret: (number | null)[];
  relation: InstitutionRelation[];
}

export interface InstitutionStat {
  rate: number | null;
  decided: number;
  same: number;
  hedge: number;
  pending: number;
  no_data: number;
  small_sample: boolean;
  start: string | null;
  end: string | null;
}

export interface InstitutionSummaryRow {
  id: InstitutionId;
  name: string;
  long: number | null;
  short: number | null;
  net: number | null;
  chg: number | null;
  relation: InstitutionRelation;
  next_ret: number | null;
}

export interface InstitutionPositions {
  as_of: string | null;
  generated_at: string | null;
  algorithm_version: string | null;
  data_state: DataState;
  stale: boolean;
  stale_since: string | null;
  stale_reason: string | null;
  institutions: InstitutionMeta[];
  varieties: { code: Exclude<InstitutionVariety, 'all'>; name: string }[];
  dates: string[];
  benchmark: { code: string; name: string; close: (number | null)[] };
  /** 仅展示用的大盘对照序列（上证指数 + 沪深300）；旧产物可缺该字段，不参与任何统计口径 */
  market_lines?: { code: string; name: string; close: (number | null)[] }[];
  series: Partial<Record<InstitutionId, Partial<Record<InstitutionVariety, InstitutionSeriesView>>>>;
  stats: Partial<Record<InstitutionId, Partial<Record<InstitutionVariety, InstitutionStat>>>>;
  summary: { date: string; institutions: InstitutionSummaryRow[] } | null;
  disclosure: { aggregation_note: string; scope_note: string; benchmark: string };
}

export interface InstitutionPositionsParams {
  institution?: InstitutionId | 'all';
  variety?: InstitutionVariety;
  from?: string;
  to?: string;
  limit?: number;
}

/** 同花顺博主动态：个股标签 */
export interface BloggerStockTag {
  name: string;
  code: string;
}

/** 同花顺博主动态：单条发文（时间均为北京时间） */
export interface BloggerPost {
  id: string;
  /** YYYY-MM-DD */
  date: string;
  /** HH:mm */
  time: string;
  text: string;
  stocks: BloggerStockTag[];
  images: number;
  /** 配图直链（同花顺 u.thsi.cn CDN，浏览器直接热链加载；历史旧数据可能为空） */
  image_urls: string[];
  url: string;
}

/** 同花顺博主动态：落盘数据只读视图 */
export interface BloggerPosts {
  author: string;
  user_id: number;
  homepage: string;
  retention_days: number;
  updated_at: string | null;
  total: number;
  posts: BloggerPost[];
}

/** 龙虎榜知名游资：席位（公开资料整理，非官方认证） */
export interface LhbSeat {
  full: string;
  broker?: string;
  addr?: string;
  confidence: 'high' | 'medium' | 'low';
}

/** 龙虎榜知名游资：单条操作记录（按 日期+代码+单日/多日 粒度去重） */
export interface LhbRecord {
  date: string;
  code: string;
  name: string;
  reasons: string[];
  is_multi_day: boolean;
  buy: number;
  sell: number;
  net: number;
  close: number | null;
  pct_chg: number | null;
  board: string | null;
  seat_hits: string[];
  confidence: 'high' | 'medium' | 'low';
}

/** 龙虎榜知名游资：区间统计（仅单日榜口径） */
export interface LhbStats {
  active_days: number;
  records: number;
  stocks: number;
  buy_total: number;
  sell_total: number;
  net_total: number;
}

/** 龙虎榜知名游资：（游资,个股）在场状态（管道判定，2026-11-13 口径） */
export interface LhbPosition {
  code: string;
  name: string;
  /** active 在场 / 确认清仓 / 疑似离场 / 强制离场兜底 */
  status: 'active' | 'exited_confirmed' | 'exited_probable' | 'exited_forced';
  /** 离场证据类型：卖出覆盖买入 / 大幅出货后未现身 / 静默期观察 / 时间兜底 */
  exit_type: 'sell_cover' | 'heavy_sell' | 'silent' | 'forced' | null;
  /** 最后一次现身（单日榜记录）日期 */
  last_seen: string;
  /** 判定成立日：确认清仓=清仓证据日；疑似/强制=静默达标交易日 */
  exit_date: string | null;
  buy_total: number;
  sell_total: number;
  /** 最后现身距数据截至日的交易日数 */
  silent_days: number;
}

/** 龙虎榜知名游资：游资条目 */
export interface LhbTrader {
  id: string;
  name: string;
  full_name: string;
  tier: string;
  style: string;
  confidence: 'high' | 'medium' | 'low';
  note: string;
  seats: LhbSeat[];
  stats: LhbStats;
  records: LhbRecord[];
  /** （游资,个股）在场状态；旧版产物无此字段 */
  positions?: LhbPosition[];
}

/** 龙虎榜知名游资：总览视图中的游资（含完整操作记录） */
export type LhbOverviewTrader = LhbTrader;

/** 龙虎榜知名游资：单日动向行 */
export interface LhbDailyRow {
  trader: string;
  trader_id: string;
  tier: string;
  code: string;
  name: string;
  buy: number;
  sell: number;
  net: number;
  pct_chg: number | null;
  board: string | null;
  confidence: 'high' | 'medium' | 'low';
  /** 该行所属（游资,个股）配对处于离场标记期内时的状态 */
  exit_status?: LhbPosition['status'];
}

/** 龙虎榜知名游资：总览响应 */
export interface LhbOverview {
  data_state: string;
  stale: boolean;
  as_of: string | null;
  generated_at: string | null;
  algorithm_version: string | null;
  seat_map_version: string | null;
  scope_note: string | null;
  trading_dates: string[];
  traders: LhbOverviewTrader[];
}

/** 龙虎榜知名游资：单游资详情响应 */
export interface LhbTraderDetail {
  data_state: string;
  as_of: string | null;
  trader: LhbTrader | null;
  records: LhbRecord[];
  total_records: number;
}

/** 龙虎榜知名游资：按日动向响应 */
export interface LhbDaily {
  data_state: string;
  as_of: string | null;
  date: string;
  rows: LhbDailyRow[];
  trading_dates: string[];
}
