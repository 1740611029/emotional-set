/** 单板块历史序列：切换板块或区间时请求，失败保留上一次结果。 */
import { useEffect, useRef, useState } from 'react';
import { ApiError, fetchSectorHistory } from './client';
import type { SectorHistoryRange } from './client';
import type { SectorHistory } from './types';

export interface SectorHistoryValue {
  history: SectorHistory | null;
  /** 最近一次成功响应对应的板块代码，用于避免切换时展示错位数据 */
  code: string | null;
  error: string | null;
  isLoading: boolean;
}

export function useSectorHistory(
  code: string | null,
  days: SectorHistoryRange,
): SectorHistoryValue {
  const [value, setValue] = useState<SectorHistoryValue>({
    history: null,
    code: null,
    error: null,
    isLoading: false,
  });
  const inFlight = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!code) return undefined;
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setValue((prev) => ({ ...prev, isLoading: true, error: null }));

    fetchSectorHistory(code, days, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setValue({ history: data, code, error: null, isLoading: false });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof ApiError ? cause.message : '板块历史加载失败';
        setValue((prev) => ({ ...prev, isLoading: false, error: message }));
      });

    return () => controller.abort();
  }, [code, days]);

  return value;
}
