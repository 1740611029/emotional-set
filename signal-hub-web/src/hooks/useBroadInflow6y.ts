/**
 * 【宽基 ETF 申购净流入·近 6 年】取数 hook：只读落盘产物，失败保留上次成功数据。
 *
 * 与既有 `useEtfShareFlow` 结构一致，互不依赖：两条数据线各自请求、各自失败、各自降级。
 */
import { useCallback, useEffect, useState } from 'react';
import { fetchBroadInflow6y } from '../data/client';
import type { BroadInflow6y } from '../lib/broadInflow6y';

interface HookState {
  data: BroadInflow6y | null;
  error: string | null;
  isLoading: boolean;
}

export function useBroadInflow6y() {
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<HookState>({ data: null, error: null, isLoading: true });

  useEffect(() => {
    const controller = new AbortController();
    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    fetchBroadInflow6y(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, error: null, isLoading: false });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : '6 年宽基申购净流入加载失败';
        setState((prev) => ({ ...prev, isLoading: false, error: message }));
      });
    return () => controller.abort();
  }, [reload]);

  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return { ...state, refresh };
}