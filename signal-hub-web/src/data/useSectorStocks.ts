/** 板块成分股快照：切换板块时请求，失败保留上一次结果；code 防止展示错位数据。 */
import { useEffect, useRef, useState } from 'react';
import { ApiError, fetchSectorStocks } from './client';
import type { SectorStocks } from './types';

export interface SectorStocksValue {
  data: SectorStocks | null;
  /** 最近一次成功响应对应的板块代码，用于避免切换板块时展示错位数据 */
  code: string | null;
  error: string | null;
  isLoading: boolean;
}

export function useSectorStocks(code: string | null): SectorStocksValue {
  const [value, setValue] = useState<SectorStocksValue>({
    data: null,
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

    fetchSectorStocks(code, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setValue({ data, code, error: null, isLoading: false });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof ApiError ? cause.message : '板块成分股加载失败';
        setValue((prev) => ({ ...prev, isLoading: false, error: message }));
      });

    return () => controller.abort();
  }, [code]);

  return value;
}