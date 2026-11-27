/** 各页数据接口客户端：超时、结构校验与错误归一化。 */
import type {
  BloggerPosts, EtfShareFlow, EtfVolumeSeries, InstitutionPositions,
  InstitutionPositionsParams, LhbDaily, LhbOverview, LhbTraderDetail,
} from './types';
import type { BroadInflow6y } from '../lib/broadInflow6y';

const TIMEOUT_MS = 10_000;

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

/** 宽基 ETF 申购净流入结构校验：缺字段即报错；202 准备中同样返回完整结构，不伪造数值。 */
export function normalizeEtfShareFlow(payload: unknown): EtfShareFlow {
  if (!isRecord(payload)) {
    throw new ApiError('宽基 ETF 申购净流入响应不是合法对象');
  }
  const state = payload.data_state;
  if (state !== 'ready' && state !== 'preparing' && state !== 'degraded') {
    throw new ApiError('宽基 ETF 申购净流入响应状态非法');
  }
  if (!Array.isArray(payload.dates) || !Array.isArray(payload.net_inflow_yi)
    || !Array.isArray(payload.index_close) || !Array.isArray(payload.universe)
    || !isRecord(payload.coverage) || !isRecord(payload.stats)) {
    throw new ApiError('宽基 ETF 申购净流入响应缺少 dates/net_inflow_yi/index_close/universe/coverage/stats');
  }
  return payload as unknown as EtfShareFlow;
}

/** 宽基 ETF 申购净流入：只读落盘产物，202 准备中同样解析为结构，不由客户端伪造数值。 */
export async function fetchEtfShareFlow(signal?: AbortSignal): Promise<EtfShareFlow> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch('/api/etf/share-flow', {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok && response.status !== 202) {
      let detail = `宽基 ETF 申购净流入接口返回 ${response.status}`;
      try {
        const body = await response.json();
        if (isRecord(body) && typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 响应体非 JSON 时保留状态码文案 */
      }
      throw new ApiError(detail, response.status);
    }
    return normalizeEtfShareFlow(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('宽基 ETF 申购净流入请求超时或已取消');
    throw new ApiError((error as Error)?.message || '宽基 ETF 申购净流入请求失败');
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

/** 手动刷新采集超时：后端同步等待 collect.js 上限 120 秒，这里留出余量。 */
const BLOGGER_REFRESH_TIMEOUT_MS = 150_000;

export type BloggerRefreshStatus = 'ok' | 'busy' | 'timeout' | 'error';

export interface BloggerRefreshResult {
  status: BloggerRefreshStatus;
  detail: string;
}

/**
 * 手动触发一轮同花顺发文采集：后端拉起 collect.js 并同步等待完成。
 * 返回后由调用方重新请求 /api/blogger/posts 取最新列表。
 */
export async function refreshBloggerPosts(signal?: AbortSignal): Promise<BloggerRefreshResult> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), BLOGGER_REFRESH_TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch('/api/blogger/refresh', {
      method: 'POST',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `手动采集接口返回 ${response.status}`;
      try {
        const body = await response.json();
        if (isRecord(body) && typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 响应体非 JSON 时保留状态码文案 */
      }
      throw new ApiError(detail, response.status);
    }
    const payload = await response.json();
    if (!isRecord(payload) || typeof payload.status !== 'string') {
      throw new ApiError('手动采集响应缺少 status');
    }
    const status = payload.status;
    if (!['ok', 'busy', 'timeout', 'error'].includes(status)) {
      throw new ApiError(`手动采集返回未知状态：${status}`);
    }
    return {
      status: status as BloggerRefreshStatus,
      detail: typeof payload.detail === 'string' ? payload.detail : '',
    };
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') {
      throw new ApiError('手动采集请求超时或已取消，稍后可重试');
    }
    throw new ApiError((error as Error)?.message || '手动采集请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

function normalizeLhb(payload: unknown, label: string): Record<string, unknown> {  if (!isRecord(payload)) throw new ApiError(`${label}响应格式错误`);
  return payload;
}

/** 龙虎榜知名游资总览：只读落盘数据，由日更管道更新。 */
export async function fetchLhbOverview(signal?: AbortSignal): Promise<LhbOverview> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch('/api/lhb/overview', {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) throw new ApiError(`龙虎榜游资接口返回 ${response.status}`, response.status);
    return normalizeLhb(await response.json(), '龙虎榜游资总览') as unknown as LhbOverview;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('龙虎榜游资请求超时或已取消');
    throw new ApiError((error as Error)?.message || '龙虎榜游资请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

/** 龙虎榜知名游资：单个游资完整记录。 */
export async function fetchLhbTrader(traderId: string, signal?: AbortSignal): Promise<LhbTraderDetail> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch(`/api/lhb/traders/${encodeURIComponent(traderId)}`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) throw new ApiError(`龙虎榜游资接口返回 ${response.status}`, response.status);
    return normalizeLhb(await response.json(), '龙虎榜游资详情') as unknown as LhbTraderDetail;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('龙虎榜游资请求超时或已取消');
    throw new ApiError((error as Error)?.message || '龙虎榜游资请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

/** 龙虎榜知名游资：某交易日全部动向。 */
export async function fetchLhbDaily(date: string, signal?: AbortSignal): Promise<LhbDaily> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch(`/api/lhb/daily/${encodeURIComponent(date)}`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) throw new ApiError(`龙虎榜游资接口返回 ${response.status}`, response.status);
    return normalizeLhb(await response.json(), '龙虎榜按日动向') as unknown as LhbDaily;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('龙虎榜游资请求超时或已取消');
    throw new ApiError((error as Error)?.message || '龙虎榜游资请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

/** 6 年宽基申购净流入结构校验：列式数组缺一即报错；202 准备中同样返回完整结构，不伪造数值。 */
export function normalizeBroadInflow6y(payload: unknown): BroadInflow6y {
  if (!isRecord(payload)) {
    throw new ApiError('6 年宽基申购净流入响应不是合法对象');
  }
  const state = payload.data_state;
  if (state !== 'ready' && state !== 'preparing' && state !== 'degraded') {
    throw new ApiError('6 年宽基申购净流入响应状态非法');
  }
  const fields = ['dates', 'daily_inflow_yi', 'cum_inflow_yi', 'index_close', 'etf_count'];
  const missing = fields.filter((key) => !Array.isArray((payload as Record<string, unknown>)[key]));
  if (missing.length > 0 || !isRecord(payload.coverage) || !isRecord(payload.stats)) {
    throw new ApiError(`6 年宽基申购净流入响应缺少 ${missing.join('/') || 'coverage/stats'}`);
  }
  return payload as unknown as BroadInflow6y;
}

/** 6 年宽基申购净流入：只读落盘产物，202 准备中同样解析为结构，不由客户端伪造数值。 */
export async function fetchBroadInflow6y(signal?: AbortSignal): Promise<BroadInflow6y> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort);
  try {
    const response = await fetch('/api/etf/broad-inflow-6y', {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok && response.status !== 202) {
      let detail = `6 年宽基申购净流入接口返回 ${response.status}`;
      try {
        const body = await response.json();
        if (isRecord(body) && typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 响应体非 JSON 时保留状态码文案 */
      }
      throw new ApiError(detail, response.status);
    }
    return normalizeBroadInflow6y(await response.json());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') throw new ApiError('6 年宽基申购净流入请求超时或已取消');
    throw new ApiError((error as Error)?.message || '6 年宽基申购净流入请求失败');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}
