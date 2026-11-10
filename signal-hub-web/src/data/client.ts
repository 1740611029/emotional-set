/** 快照接口客户端：超时、结构校验与错误归一化。 */
import type {
  BloggerPosts, EtfVolumeSeries, InstitutionPositions, InstitutionPositionsParams, SectorHistory, Snapshot,
} from './types';

const ENDPOINT = '/api/snapshot';
const TIMEOUT_MS = 10_000;
export const SECTOR_HISTORY_RANGES = [60, 120, 250] as const;
export type SectorHistoryRange = (typeof SECTOR_HISTORY_RANGES)[number];

export class ApiError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function normalize(payload: unknown): Snapshot {
  if (!isRecord(payload)) {
    throw new ApiError('响应不是合法对象');
  }
  const mode = payload.mode;
  if (mode !== 'live' && mode !== 'close' && mode !== null) {
    throw new ApiError(`未知运行模式：${String(mode)}`);
  }
  if (!Array.isArray(payload.indices) || !Array.isArray(payload.recent_signals)) {
    throw new ApiError('响应缺少 indices / recent_signals');
  }
  if (!isRecord(payload.sectors)) {
    throw new ApiError('响应缺少 sectors');
  }
  return {
    ...(payload as unknown as Snapshot),
    candidates: Array.isArray(payload.candidates) ? (payload.candidates as Snapshot['candidates']) : [],
  };
}

export async function fetchSnapshot(signal?: AbortSignal): Promise<Snapshot> {
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
    if (response.status === 202) {
      return normalize(await response.json());
    }
    if (!response.ok) {
      throw new ApiError(`数据服务返回 ${response.status}`, response.status);
    }
    return normalize(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') {
      throw new ApiError('请求超时或已取消');
    }
    throw new ApiError((error as Error)?.message || '网络请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

/** ETF 成交额序列结构校验：缺字段即报错；202 准备中同样返回完整结构，不伪造数值。 */
export function normalizeEtfVolumeSeries(payload: unknown): EtfVolumeSeries {
  if (!isRecord(payload)) {
    throw new ApiError('ETF 成交额序列响应不是合法对象');
  }
  const state = payload.data_state;
  if (state !== 'ready' && state !== 'preparing' && state !== 'degraded') {
    throw new ApiError('ETF 成交额序列响应状态非法');
  }
  if (!Array.isArray(payload.dates) || !Array.isArray(payload.etfs)
    || !Array.isArray(payload.total_amount) || !Array.isArray(payload.index_close)
    || !Array.isArray(payload.l3_dates) || !isRecord(payload.coverage)) {
    throw new ApiError('ETF 成交额序列响应缺少 dates/etfs/total_amount/index_close/l3_dates/coverage');
  }
  return payload as unknown as EtfVolumeSeries;
}

/** ETF 成交额序列：一次拉全齐备区间，前端本地切档；202 也解析为准备中状态。 */
export async function fetchEtfVolumeSeries(signal?: AbortSignal): Promise<EtfVolumeSeries> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch('/api/etf/volume-series', {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok && response.status !== 202) {
      let detail = `ETF 成交额序列接口返回 ${response.status}`;
      try {
        const body = await response.json();
        if (isRecord(body) && typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 响应体非 JSON 时保留状态码文案 */
      }
      throw new ApiError(detail, response.status);
    }
    return normalizeEtfVolumeSeries(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('ETF 成交额序列请求超时或已取消');
    throw new ApiError((error as Error)?.message || 'ETF 成交额序列请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

function normalizeSectorHistory(payload: unknown): SectorHistory {
  if (!isRecord(payload)) {
    throw new ApiError('板块历史响应不是合法对象');
  }
  if (!Array.isArray(payload.series)) {
    throw new ApiError('板块历史响应缺少 series');
  }
  return payload as unknown as SectorHistory;
}

/** 单板块历史序列；服务端未就绪时抛错，由调用方决定降级。 */
export async function fetchSectorHistory(
  code: string,
  days: SectorHistoryRange,
  signal?: AbortSignal,
): Promise<SectorHistory> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch(`/api/sectors/${encodeURIComponent(code)}/history?days=${days}`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `板块历史接口返回 ${response.status}`;
      try {
        const body = await response.json();
        if (isRecord(body) && typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 响应体非 JSON 时保留状态码文案 */
      }
      throw new ApiError(detail, response.status);
    }
    return normalizeSectorHistory(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') {
      throw new ApiError('板块历史请求超时或已取消');
    }
    throw new ApiError((error as Error)?.message || '板块历史请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

function normalizeInstitutionPositions(payload: unknown): InstitutionPositions {
  if (!isRecord(payload) || !Array.isArray(payload.dates) || !isRecord(payload.series)) {
    throw new ApiError('机构持仓响应缺少 dates / series');
  }
  if (payload.data_state !== 'ready' && payload.data_state !== 'preparing' && payload.data_state !== 'degraded') {
    throw new ApiError('机构持仓响应状态非法');
  }
  return payload as unknown as InstitutionPositions;
}

/** 机构持仓日频数据：202 准备中同样返回结构，不由客户端伪造数值。 */
export async function fetchInstitutionPositions(
  params: InstitutionPositionsParams = {},
  signal?: AbortSignal,
): Promise<InstitutionPositions> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  const query = new URLSearchParams();
  if (params.institution) query.set('institution', params.institution);
  if (params.variety) query.set('variety', params.variety);
  if (params.from) query.set('from', params.from);
  if (params.to) query.set('to', params.to);
  if (params.limit) query.set('limit', String(params.limit));
  try {
    const response = await fetch(`/api/institutions/positions?${query.toString()}`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok && response.status !== 202) {
      let detail = `机构持仓接口返回 ${response.status}`;
      try {
        const body = await response.json();
        if (isRecord(body) && typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 响应体非 JSON 时保留状态码文案 */
      }
      throw new ApiError(detail, response.status);
    }
    return normalizeInstitutionPositions(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('机构持仓请求超时或已取消');
    throw new ApiError((error as Error)?.message || '机构持仓请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

function normalizeBloggerPosts(payload: unknown): BloggerPosts {
  if (!isRecord(payload) || !Array.isArray(payload.posts)) {
    throw new ApiError('博主动态响应缺少 posts');
  }
  return payload as unknown as BloggerPosts;
}

/** 同花顺博主动态：只读落盘数据，由采集脚本定时更新。 */
export async function fetchBloggerPosts(signal?: AbortSignal): Promise<BloggerPosts> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch('/api/blogger/posts', {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `博主动态接口返回 ${response.status}`;
      try {
        const body = await response.json();
        if (isRecord(body) && typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 响应体非 JSON 时保留状态码文案 */
      }
      throw new ApiError(detail, response.status);
    }
    return normalizeBloggerPosts(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('博主动态请求超时或已取消');
    throw new ApiError((error as Error)?.message || '博主动态请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}
