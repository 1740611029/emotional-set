import { useCallback, useEffect, useState } from 'react';
import { fetchEtfVolumeSeries } from '../data/client';
import type { EtfVolumeSeries } from '../data/types';

interface HookState {
  data: EtfVolumeSeries | null;
  error: string | null;
  isLoading: boolean;
}

/**
 * ETF 成交额序列：首次进入请求一次，失败保留上一次成功数据，手动刷新重取。
 * 时间范围切换只在前端切片，不触发网络请求（由页面本地状态驱动，与本 hook 无关）。
 */
export function useEtfVolumeSeries() {
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<HookState>({ data: null, error: null, isLoading: true });

  useEffect(() => {
    const controller = new AbortController();
    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    fetchEtfVolumeSeries(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, error: null, isLoading: false });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : 'ETF 成交额序列加载失败';
        setState((prev) => ({ ...prev, isLoading: false, error: message }));
      });
    return () => controller.abort();
  }, [reload]);

  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return { ...state, refresh };
}
