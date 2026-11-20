/**
 * 个股日线（日 K 线 + 成交量）数据契约与只读客户端。
 *
 * 口径（用户 2026-11-20 确认）：
 * - 点击连板个股时实时请求 /api/stock/daily/{code}，新浪财经不复权、近 60 交易日；
 * - 后端负责取数与字段映射，前端只做展示，不生成、不推算、不补齐；
 * - 失败返回 data_state=error + error 文案，前端如实展示，绝不填充演示数值。
 */
import { ApiError } from './client';

export type StockDailyState = 'ready' | 'error';

export interface StockDailyPoint {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  /** 涨跌幅（百分数），接口未返回时为 null */
  change_pct: number | null;
  /** 成交量（手），接口未返回时为 null */
  volume: number | null;
}

export interface StockDailyPayload {
  code: string;
  data_state: StockDailyState;
  source: string | null;
  adjust: string | null;
  as_of: string | null;
  error: string | null;
  points: StockDailyPoint[];
}

const TIMEOUT_MS = 15_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** 结构校验：状态非法、points 缺失或 OHLC 非法即报错，不静默降级为假数据。 */
export function normalizeStockDaily(payload: unknown): StockDailyPayload {
  if (!isRecord(payload)) throw new ApiError('个股日线响应不是合法对象');
  const state = payload.data_state;
  if (state !== 'ready' && state !== 'error') throw new ApiError('个股日线响应状态非法');
  if (!Array.isArray(payload.points)) throw new ApiError('个股日线响应缺少 points');

  const points = payload.points as StockDailyPoint[];
  if (state === 'ready') {
    if (!points.length) throw new ApiError('个股日线响应无有效交易日');
    for (const point of points) {
      if (
        !isRecord(point)
        || typeof point.date !== 'string'
        || !isFiniteNumber(point.open)
        || !isFiniteNumber(point.close)
        || !isFiniteNumber(point.high)
        || !isFiniteNumber(point.low)
      ) {
        throw new ApiError('个股日线 OHLC 字段非法');
      }
    }
  }

  return {
    code: typeof payload.code === 'string' ? payload.code : '',
    data_state: state,
    source: typeof payload.source === 'string' ? payload.source : null,
    adjust: typeof payload.adjust === 'string' ? payload.adjust : null,
    as_of: typeof payload.as_of === 'string' ? payload.as_of : null,
    error: typeof payload.error === 'string' ? payload.error : null,
    points,
  };
}

/** 只读拉取；超时/取消统一转为明确的错误信息。 */
export async function fetchStockDaily(code: string, signal?: AbortSignal): Promise<StockDailyPayload> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch(`/api/stock/daily/${encodeURIComponent(code)}`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `个股日线接口返回 ${response.status}`;
      try {
        const body = await response.json();
        if (isRecord(body) && typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 响应体非 JSON 时保留状态码文案 */
      }
      throw new ApiError(detail, response.status);
    }
    return normalizeStockDaily(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('个股日线请求超时或已取消');
    throw new ApiError((error as Error)?.message || '个股日线请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}
