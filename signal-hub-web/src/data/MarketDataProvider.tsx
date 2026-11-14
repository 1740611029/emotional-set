/** 统一数据层：30 秒轮询、页面可见性控制、失败保留上次数据。 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { ApiError, fetchSnapshot } from './client';
import { useTaskControl } from './TaskControlProvider';
import type { Snapshot } from './types';

// 矩阵聚合为纯计算模块，单独导出便于不依赖 React 的单元测试直接引用。
export {
  PRECISION_HORIZONS, PRECISION_KINDS, precisionMatrix, wilsonInterval,
} from '../lib/precisionMatrix';
export type { MatrixCellView, MatrixView } from '../lib/precisionMatrix';

export const REFRESH_INTERVAL_MS = 30_000;

interface MarketDataValue {
  snapshot: Snapshot | null;
  error: string | null;
  isLoading: boolean;
  lastSuccessAt: string | null;
  refresh: () => void;
}

const MarketDataContext = createContext<MarketDataValue | null>(null);

function nowLabel(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

export function MarketDataProvider({ children }: { children: ReactNode }) {
  const { enabled } = useTaskControl();
  const pollEnabled = enabled.snapshot_poll;
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [lastSuccessAt, setLastSuccessAt] = useState<string | null>(null);
  const [visible, setVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState === 'visible',
  );
  const inFlight = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    try {
      const data = await fetchSnapshot(controller.signal);
      setSnapshot(data);
      setError(null);
      setLastSuccessAt(nowLabel());
    } catch (cause) {
      if (controller.signal.aborted) return;
      const message = cause instanceof ApiError ? cause.message : '数据加载失败';
      setError(message);
    } finally {
      if (!controller.signal.aborted) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const onVisibility = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  // 首次加载与「切回页面」时各取一次；停止定时任务后仍保留这一步与手动刷新
  useEffect(() => {
    if (!visible) return undefined;
    void load();
    return () => inFlight.current?.abort();
  }, [visible, load]);

  // 30 秒自动轮询单独受任务开关控制：关闭时不再创建定时器
  useEffect(() => {
    if (!visible || !pollEnabled) return undefined;
    const timer = window.setInterval(() => void load(), REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [visible, load, pollEnabled]);

  const value = useMemo<MarketDataValue>(
    () => ({ snapshot, error, isLoading, lastSuccessAt, refresh: () => void load() }),
    [snapshot, error, isLoading, lastSuccessAt, load],
  );

  return <MarketDataContext.Provider value={value}>{children}</MarketDataContext.Provider>;
}

export function useMarketData(): MarketDataValue {
  const value = useContext(MarketDataContext);
  if (!value) {
    throw new Error('useMarketData 必须在 MarketDataProvider 内使用');
  }
  return value;
}

/** 旧口径（任意时点均线反转）汇总；保留供兼容读取，新页面主口径改用多持有期矩阵。 */
export function overallAccuracy(snapshot: Snapshot | null): {
  rate: number | null;
  resolved: number;
  successes: number;
  start: string | null;
  end: string | null;
  avgDrawdown: number | null;
  worstDrawdown: number | null;
  avgForwardReturn20: number | null;
} {
  if (!snapshot || snapshot.indices.length === 0) {
    return {
      rate: null, resolved: 0, successes: 0, start: null, end: null,
      avgDrawdown: null, worstDrawdown: null, avgForwardReturn20: null,
    };
  }
  let resolved = 0;
  let successes = 0;
  let drawdownWeighted = 0;
  let drawdownWeight = 0;
  let worstDrawdown: number | null = null;
  let returnWeighted = 0;
  let returnWeight = 0;
  for (const item of snapshot.indices) {
    for (const segment of item.evaluation?.segments ?? []) {
      for (const kind of ['bottom', 'top'] as const) {
        const summary = segment.summary?.[kind];
        if (!summary) continue;
        resolved += summary.resolved;
        successes += summary.successes;
        if (summary.avg_max_adverse_excursion !== null && summary.avg_max_adverse_excursion !== undefined) {
          drawdownWeighted += summary.avg_max_adverse_excursion * summary.resolved;
          drawdownWeight += summary.resolved;
          worstDrawdown =
            worstDrawdown === null
              ? summary.worst_max_adverse_excursion ?? null
              : Math.min(worstDrawdown, summary.worst_max_adverse_excursion ?? worstDrawdown);
        }
        if (summary.avg_forward_return_20 !== null && summary.avg_forward_return_20 !== undefined) {
          returnWeighted += summary.avg_forward_return_20 * summary.resolved;
          returnWeight += summary.resolved;
        }
      }
    }
  }
  return {
    rate: resolved > 0 ? (successes / resolved) * 100 : null,
    resolved,
    successes,
    start: snapshot.indices[0]?.accuracy?.start ?? null,
    end: snapshot.indices[0]?.accuracy?.end ?? null,
    avgDrawdown: drawdownWeight > 0 ? drawdownWeighted / drawdownWeight : null,
    worstDrawdown,
    avgForwardReturn20: returnWeight > 0 ? returnWeighted / returnWeight : null,
  };
}
