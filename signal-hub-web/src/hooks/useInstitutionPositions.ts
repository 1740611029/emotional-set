import { useCallback, useEffect, useState } from 'react';
import { fetchInstitutionPositions } from '../data/client';
import type { InstitutionPositions, InstitutionPositionsParams } from '../data/types';

interface HookState {
  data: InstitutionPositions | null;
  error: string | null;
  isLoading: boolean;
}

/** 日频数据只在参数变化或手动刷新时请求；失败保留上一次成功数据。 */
export function useInstitutionPositions(params: InstitutionPositionsParams) {
  const { institution = 'all', variety = 'all', from, to, limit = 120 } = params;
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<HookState>({ data: null, error: null, isLoading: true });

  useEffect(() => {
    const controller = new AbortController();
    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    fetchInstitutionPositions({ institution, variety, from, to, limit }, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, error: null, isLoading: false });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : '机构持仓加载失败';
        setState((prev) => ({ ...prev, isLoading: false, error: message }));
      });
    return () => controller.abort();
  }, [institution, variety, from, to, limit, reload]);

  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return { ...state, refresh };
}
