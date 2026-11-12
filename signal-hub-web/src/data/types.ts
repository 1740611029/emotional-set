/** 与后端 `/api/snapshot` 契约对齐的类型定义。 */

export type SignalKind = 'bottom' | 'top';
export type SignalType = SignalKind | 'none';
export type DataMode = 'live' | 'close';
export type DataState = 'ready' | 'preparing' | 'degraded';

export interface Signal {
  date: string;
  type: SignalKind;
  price: number;
  candidate_date?: string;
  grade?: 'A' | 'B';
  score?: number | null;
  core_hits?: number;
  factors?: Record<string, number | null>;
  breadth?: { breadth20: number | null; delta5: number | null };
  confirmation?: ConfirmationStep[];
}

export interface ConfirmationStep {
  date: string;
  offset: number;
  price: boolean;
  momentum: boolean;
  volume: boolean;
  passed: number;
  close: number;
}

export interface AccuracyBucket {
  triggers: number;
  successes: number;
  /** 百分数，0-100 */
  rate: number;
  /** 小数，负数表示不利方向；null 表示无已判定样本 */
  avg_max_adverse_excursion: number | null;
  worst_max_adverse_excursion: number | null;
  avg_forward_return_20: number | null;
  return_drawdown_ratio: number | null;
}

export interface Accuracy {
  start: string;
  end: string;
  bottom: AccuracyBucket;
  top: AccuracyBucket;
}

export interface CandidateLayers {
  price: boolean;
  momentum: boolean;
  volume: boolean;
}

export interface Candidate {
  date: string;
  type: SignalKind;
  price: number;
  layers: CandidateLayers;
  /** 已通过层数 0-3 */
  passed: number;
  trading_days_since: number;
  code?: string;
  name?: string;
}

export interface EvaluationSummary {
  total: number;
  resolved: number;
  successes: number;
  failures: number;
  pending: number;
  rate: number | null;
  small_sample: boolean;
  note: string | null;
  avg_max_adverse_excursion: number | null;
  worst_max_adverse_excursion: number | null;
  avg_forward_return_20: number | null;
  return_drawdown_ratio: number | null;
}

export interface BaselineBlock {
  horizon_days: number;
  samples: number;
  successes: number;
  rate: number | null;
  signal_rate_same_horizon: number | null;
  seed: number;
}

export interface EvaluationSegment {
  segment: string;
  independent: boolean;
  start: string | null;
  end: string | null;
  summary: Record<SignalKind, EvaluationSummary>;
  baseline?: Record<SignalKind, BaselineBlock>;
  events: unknown[];
}

export interface Evaluation {
  algorithm_version: string;
  research_status: string;
  note: string;
  segments: EvaluationSegment[];
}

export interface HistoryPoint {
  date: string;
  close: number;
}

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

/** 情绪底背景带：超卖 + 宽度冰点的连续区间（浅色底纹，非信号点） */
export interface BottomZone {
  start_date: string;
  end_date: string;
  days: number;
  has_divergence: boolean;
}

export interface IndexItem {
  code: string;
  name: string;
  latest: number;
  change_pct: number;
  current_signal: { type: SignalType; date: string | null };
  history: HistoryPoint[];
  signals: Signal[];
  evaluation?: Evaluation;
  accuracy: Accuracy;
  candidates: Candidate[];
  provisional: boolean;
  precision_matrix?: PrecisionMatrix;
  /** 情绪底背景带区间；旧版产物可能缺失 */
  bottom_zones?: BottomZone[];
  open?: number;
  high?: number;
  low?: number;
  volume?: number;
}

export type SectorState = 'bull' | 'warn' | 'bear' | 'rise';
export type SectorStateBasis = 'directional' | 'neutral';

export interface SectorStrengthParts {
  /** 收盘价在近 20 日高低区间中的位置分，0-100 */
  range_position: number;
  /** 相对 60 日均线偏离的映射分，0-100 */
  ma_score: number;
}

/** 单层行业板块：每个板块都是一条独立判定的日线，无层级与父子关系 */
export interface SectorItem {
  code: string;
  name: string;
  date: string | null;
  state: SectorState | null;
  strength: number | null;
  strength_parts: SectorStrengthParts | null;
  strength_basis: SectorStateBasis | null;
  days: number | null;
  data_anomaly: boolean;
  data_lag: boolean;
  spark: number[];
}

export type SectorGroupKey = SectorState | 'unclassified';

/** 板块方向评估的单格：同一批事件按四态之一、某一前瞻窗口统计 */
export interface SectorStateEvaluation {
  /** 该格的事件条目数（含尚未走完窗口的未决项） */
  entries: number;
  /** 已走完窗口的样本数，即胜率分母 */
  samples: number;
  successes: number;
  pending: number;
  /** 0-1 小数，方向按状态期望（bull/rise 涨、warn/bear 跌）；无已判定样本时为 null */
  rate: number | null;
  /** Wilson 95% 区间，同为 0-1 小数；无样本时为 [null, null] */
  wilson95: [number | null, number | null];
  small_sample: boolean;
  note: string | null;
}

export type SectorEvalHorizon = '10' | '20';

export interface SectorEvaluationSegment {
  segment: 'research' | 'exploratory_validation';
  /** 验证段为事后观察，当前恒为 false */
  independent: boolean;
  states: Record<SectorState, Record<SectorEvalHorizon, SectorStateEvaluation | null>>;
}

export interface SectorEvaluation {
  algorithm_version: string;
  research_status: string;
  /** 胜率单位为小数（fraction），展示时再转百分数 */
  rate_unit: 'fraction';
  boundary_policy: string;
  note: string;
  segments: SectorEvaluationSegment[];
}

export interface SectorBlock {
  as_of: string | null;
  /** 最近已完成交易日（服务端按交易日历判定，日历缺失时为工作日近似） */
  expected_as_of?: string | null;
  expected_basis?: 'calendar' | 'workday' | null;
  /** 板块数据落后最近已完成交易日的交易日数；null 表示板块尚未就绪 */
  lag_days?: number | null;
  is_mock?: boolean;
  algorithm_version?: string | null;
  groups: Partial<Record<SectorGroupKey, SectorItem[]>>;
  /** 板块级四态方向评估；未生成时为 null */
  evaluation?: SectorEvaluation | null;
}

export interface SectorHistoryPoint {
  date: string;
  close: number | null;
  state: SectorState | null;
}

export interface SectorHistory {
  code: string;
  name: string;
  as_of: string | null;
  date: string | null;
  requested_days: number;
  state: SectorState | null;
  strength: number | null;
  strength_parts: SectorStrengthParts | null;
  strength_basis: SectorStateBasis | null;
  days: number | null;
  data_anomaly: boolean;
  data_lag: boolean;
  series: SectorHistoryPoint[];
  insufficient: boolean;
  coverage: { start: string | null; end: string | null; points: number };
  stale: boolean;
  stale_since: string | null;
}

/** 多持有期准确率矩阵（index-precision-pivot-signals）。 */
export type PrecisionSegment = 'train' | 'validation';
export type PrecisionHorizon = '5' | '10' | '20' | '30';

export interface PrecisionCell {
  segment: PrecisionSegment;
  horizon_days: number;
  direction: SignalKind;
  statistical_interval: { start: string | null; end: string | null };
  triggers: number;
  resolved: number;
  n_resolved: number;
  successes: number;
  failures: number;
  pending: number;
  direction_correct_rate: number | null;
  direction_correct_wilson95: [number | null, number | null];
  effective_amplitude_successes: number;
  effective_amplitude_rate: number | null;
  effective_amplitude_wilson95: [number | null, number | null];
  effective_amplitude_line: number;
  small_sample: boolean;
  baseline: {
    baseline_samples: number;
    baseline_successes: number;
    baseline_rate: number | null;
    signal_rate: number | null;
    difference: number | null;
    seed: number;
    horizon_days: number;
  };
  drawdown: {
    avg_max_adverse_excursion: number | null;
    worst_max_adverse_excursion: number | null;
    avg_forward_return_20: number | null;
    avg_forward_return_30: number | null;
    return_drawdown_ratio: number | null;
    mae_exceeded_failures: number;
    mae_exceeded_failure_share: number | null;
  };
}

export interface PrecisionMatrixSegment {
  statistical_interval: { start: string | null; end: string | null };
  triggers: number;
  horizons: Record<PrecisionHorizon, Record<SignalKind, PrecisionCell>>;
}

export interface PrecisionMatrix {
  train: PrecisionMatrixSegment;
  validation: PrecisionMatrixSegment;
}

export interface PrecisionPivotAnchor {
  date: string;
  type: SignalKind;
  price: number;
  pivot_date: string;
  pivot_price: number;
  natural_confirm_date: string;
  natural_confirm_offset: number;
  signal_confirm_offset_from_pivot: number;
  signal_confirm_offset_from_natural: number;
  confirmation: unknown[];
}

export interface PrecisionBlock {
  algorithm_version: string;
  signal_algorithm_version: string;
  research_status: string;
  covered_universe: string[];
  universe_size: number;
  segments: Record<string, unknown>;
  horizons: number[];
  criteria_note: string;
  legacy_rate_note: string;
  effective_amplitude_lines: Record<string, Record<string, number>>;
  decision: string;
  pivot_anchors: Record<string, PrecisionPivotAnchor[]>;
  comparison: Record<string, unknown>;
}

export interface Snapshot {
  mode: DataMode | null;
  as_of: string | null;
  generated_at: string | null;
  stale: boolean;
  stale_since: string | null;
  stale_reason: string | null;
  provisional: boolean;
  data_state: DataState;
  algorithm_version: string | null;
  indices: IndexItem[];
  sectors: SectorBlock;
  recent_signals: (Signal & {
    code: string;
    name: string;
    change_since: number;
    grade?: 'A' | 'B';
    max_drawdown_20?: number | null;
  })[];
  candidates: Candidate[];
  /** 统计覆盖范围、口径说明与拐点锚点；旧版产物可能缺失，页面须兼容 */
  precision?: PrecisionBlock | null;
  progress?: { stage: string; detail: string; started_at: string | null };
}

/** 三层确认的中文名称与展示顺序 */
export const LAYER_LABELS: { key: keyof CandidateLayers; label: string }[] = [
  { key: 'price', label: '价格' },
  { key: 'momentum', label: '动量' },
  { key: 'volume', label: '量能' },
];

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
