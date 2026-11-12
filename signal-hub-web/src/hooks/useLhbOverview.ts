import { useCallback, useEffect, useState } from 'react';
import { fetchLhbOverview } from '../data/client';
import type { LhbOverview } from '../data/types';

interface HookState {
  data: LhbOverview | null;
  error: string | null;
  isLoading: boolean;
}

/**
 * 龙虎榜知名游资总览：进入页面请求一次，失败保留上一次成功数据，支持手动刷新。
 * 数据由服务端晚间日更管道写入 lhb-traders.json，前端只读不触发抓取。
 */
export function useLhbOverview() {
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<HookState>({ data: null, error: null, isLoading: true });

  useEffect(() => {
    const controller = new AbortController();
    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    fetchLhbOverview(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, error: null, isLoading: false });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : '龙虎榜游资数据加载失败';
        setState((prev) => ({ ...prev, isLoading: false, error: message }));
      });
    return () => controller.abort();
  }, [reload]);

  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return { ...state, refresh };
}
