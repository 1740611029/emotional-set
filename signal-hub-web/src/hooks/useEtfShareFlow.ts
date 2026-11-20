import { useCallback, useEffect, useState } from 'react';
import { fetchEtfShareFlow } from '../data/client';
import type { EtfShareFlow } from '../data/types';

interface HookState {
  data: EtfShareFlow | null;
  error: string | null;
  isLoading: boolean;
}

/** 宽基 ETF 申购净流入：首次进入请求一次，失败保留上次成功数据，手动刷新重取。 */
export function useEtfShareFlow() {
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<HookState>({ data: null, error: null, isLoading: true });

  useEffect(() => {
    const controller = new AbortController();
    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    fetchEtfShareFlow(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, error: null, isLoading: false });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : '宽基 ETF 申购净流入加载失败';
        setState((prev) => ({ ...prev, isLoading: false, error: message }));
      });
    return () => controller.abort();
  }, [reload]);

  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return { ...state, refresh };
}
