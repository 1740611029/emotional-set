import { useCallback, useEffect, useState } from 'react';
import { fetchLimitCycle } from '../data/limitCycle';
import type { LimitCyclePayload } from '../data/limitCycle';

interface HookState {
  data: LimitCyclePayload | null;
  error: string | null;
  isLoading: boolean;
}

/**
 * 连板周期数据：首次进入请求一次，失败保留上一次成功数据，手动刷新重取。
 * 与 ETF 成交额序列保持同一取数模式；preparing 由页面渲染待接入状态。
 */
export function useLimitCycle() {
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<HookState>({ data: null, error: null, isLoading: true });

  useEffect(() => {
    const controller = new AbortController();
    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    fetchLimitCycle(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, error: null, isLoading: false });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : '连板周期数据加载失败';
        setState((prev) => ({ ...prev, isLoading: false, error: message }));
      });
    return () => controller.abort();
  }, [reload]);

  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return { ...state, refresh };
}
