import { useCallback, useEffect, useState } from 'react';
import { fetchBloggerPosts } from '../data/client';
import type { BloggerPosts } from '../data/types';

interface HookState {
  data: BloggerPosts | null;
  error: string | null;
  isLoading: boolean;
}

/**
 * 同花顺博主动态：进入页面请求一次，失败保留上一次成功数据，支持手动刷新。
 * 采集由本机计划任务完成（交易时段 5 分钟、其余 30 分钟），前端只读不触发抓取。
 */
export function useBloggerPosts() {
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<HookState>({ data: null, error: null, isLoading: true });

  useEffect(() => {
    const controller = new AbortController();
    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    fetchBloggerPosts(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, error: null, isLoading: false });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : '博主动态加载失败';
        setState((prev) => ({ ...prev, isLoading: false, error: message }));
      });
    return () => controller.abort();
  }, [reload]);

  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return { ...state, refresh };
}
