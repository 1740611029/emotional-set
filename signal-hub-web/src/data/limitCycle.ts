/**
 * 连板周期（/limit-cycle）数据契约与只读客户端。
 *
 * 口径约束（AGENTS.md §1 / §5 / §10）：
 * - 所有数值必须由数据管道产物提供，前端只做展示映射，不生成、不推算、不补齐；
 * - 产物缺失或 data_state=preparing 时返回全空结构，绝不填充演示数值；
 * - phase.observed 只允许包含已发生的交易日，前端不得据此推断未来走势；
 * - 五阶段色带仅作版式参考，不做阶段归类、不使用阈值。
 */
import { ApiError } from './client';

export type LimitCycleState = 'ready' | 'preparing';

export interface LimitCyclePoint {
  date: string;
  /** 原始客观值（最高板数 / 涨停家数），由管道提供；前端只做线性映射到 y 坐标 */
  value: number;
}

/** 当日客观读数，含样本量说明；不是任何信号的判定依据 */
export interface LimitCycleBasisItem {
  label: string;
  value: string | null;
  /** 样本量说明，如 "n=127"；无可为空 */
  sample: string | null;
}

/** 数据覆盖情况：东财股池历史不可回溯，缺失必须如实披露 */
export interface LimitCycleCoverage {
  requested_days: number;
  valid_days: number;
  start: string | null;
  end: string | null;
  note: string | null;
}

export interface LimitCyclePhase {
  /** 五阶段名称，仅作版式参考，不参与任何判定 */
  stages: string[];
  /** 曲线所用客观指标名称 */
  curve_metric: string;
  curve_unit: string;
  /** 固定 y 轴上限，不做样本归一化，避免未来函数 */
  curve_max: number;
  /** 已发生交易日序列；严禁包含未来或未确认区间 */
  observed: LimitCyclePoint[];
  /** 当日客观读数 */
  readouts: LimitCycleBasisItem[];
}

export interface LimitCycleTemperature {
  /** 展示的客观指标名称，如「当日涨停家数」 */
  metric: string;
  unit: string;
  value: number | null;
  change: number | null;
  /** 当日值在有效交易日区间内的百分位排名，0–100 */
  percentile: number | null;
  /** 近 7 个有效交易日序列 */
  series: LimitCyclePoint[];
  formula: string | null;
}

export interface LimitCycleLadderRow {
  /** 1 = 首板 */
  board: number;
  count: number;
}

export interface LimitCycleStock {
  code: string;
  name: string;
  boards: number;
  sector: string | null;
  /** 封单金额，万元 */
  seal_amount: number | null;
  /** 首次封板时间 HH:mm */
  first_seal: string | null;
  status: string | null;
}

export interface LimitCycleMetrics {
  max_board: number | null;
  max_board_leader: string | null;
  continuous_count: number | null;
  continuous_change: number | null;
  limit_up_count: number | null;
  broken_count: number | null;
  /** 2→3 板晋级率（百分数），必须与样本量、统计区间同时展示 */
  promote_rate: number | null;
  promote_sample: number | null;
  promote_window: string | null;
}

export interface LimitCyclePayload {
  data_state: LimitCycleState;
  as_of: string | null;
  generated_at: string | null;
  /** 统计口径全文，由数据管道填写；前端不自行编写口径 */
  methodology: string | null;
  coverage: LimitCycleCoverage | null;
  phase: LimitCyclePhase | null;
  temperature: LimitCycleTemperature | null;
  ladder: LimitCycleLadderRow[];
  stocks: LimitCycleStock[];
  metrics: LimitCycleMetrics | null;
}

/** 五阶段名称：产品概念常量，不是行情数据 */
export const LIMIT_CYCLE_STAGES = ['冰点', '修复', '发酵', '高潮', '退潮'];

/** 分位刻度分段色（低 → 高），仅本页使用 */
export const TEMPERATURE_SCALE = ['#e0efff', '#cfe9da', '#fbe4dc', '#f6bdb0', '#e07352'];

/** 连板梯队色阶，板级越高越深；仅本页使用，不改动全局色彩系统 */
export const BOARD_COLORS = ['#f9d5c0', '#f3b795', '#eb9670', '#e07352', '#d4543f', '#c0392b'];

const ENDPOINT = '/api/limit-cycle';
const TIMEOUT_MS = 10_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function emptyLimitCyclePayload(): LimitCyclePayload {
  return {
    data_state: 'preparing',
    as_of: null,
    generated_at: null,
    methodology: null,
    coverage: null,
    phase: null,
    temperature: null,
    ladder: [],
    stocks: [],
    metrics: null,
  };
}

/** 结构校验：状态非法或缺关键容器即报错，不静默降级为假数据。 */
export function normalizeLimitCycle(payload: unknown): LimitCyclePayload {
  if (!isRecord(payload)) {
    throw new ApiError('连板周期响应不是合法对象');
  }
  const state = payload.data_state;
  if (state !== 'ready' && state !== 'preparing') {
    throw new ApiError('连板周期响应状态非法');
  }
  if (!Array.isArray(payload.ladder) || !Array.isArray(payload.stocks)) {
    throw new ApiError('连板周期响应缺少 ladder / stocks');
  }
  if (state === 'preparing') {
    return emptyLimitCyclePayload();
  }
  if (payload.phase !== null && !isRecord(payload.phase)) {
    throw new ApiError('连板周期响应 phase 结构非法');
  }
  if (payload.temperature !== null && !isRecord(payload.temperature)) {
    throw new ApiError('连板周期响应 temperature 结构非法');
  }
  if (payload.metrics !== null && !isRecord(payload.metrics)) {
    throw new ApiError('连板周期响应 metrics 结构非法');
  }
  return {
    ...(payload as unknown as LimitCyclePayload),
    coverage: isRecord(payload.coverage)
      ? (payload.coverage as unknown as LimitCyclePayload['coverage'])
      : null,
    ladder: payload.ladder as LimitCycleLadderRow[],
    stocks: payload.stocks as LimitCycleStock[],
  };
}

/** 只读拉取；202「准备中」按正常响应解析为空结构。 */
export async function fetchLimitCycle(signal?: AbortSignal): Promise<LimitCyclePayload> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch(ENDPOINT, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok && response.status !== 202) {
      let detail = `连板周期接口返回 ${response.status}`;
      try {
        const body = await response.json();
        if (isRecord(body) && typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 响应体非 JSON 时保留状态码文案 */
      }
      throw new ApiError(detail, response.status);
    }
    return normalizeLimitCycle(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('连板周期请求超时或已取消');
    throw new ApiError((error as Error)?.message || '连板周期请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}
