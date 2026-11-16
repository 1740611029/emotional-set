import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchBloggerPosts, refreshBloggerPosts } from '../data/client';
import type { BloggerPosts } from '../data/types';

interface HookState {
  data: BloggerPosts | null;
  error: string | null;
  isLoading: boolean;
  /** 手动刷新是否正在触发采集（采集完成后自动重拉列表） */
  isRefreshing: boolean;
  /** 手动采集的结果提示：busy/timeout/error 时给出非阻断文案 */
  refreshNotice: string | null;
}

/**
 * 同花顺博主动态：进入页面请求一次，失败保留上一次成功数据，支持手动刷新。
 * 手动刷新 = 后端拉起 collect.js 同步采集（上限约 2 分钟），完成后自动重拉落盘列表。
 * 后台仍由本机计划任务定时采集；busy 状态说明另有采集在跑，直接复用其落盘结果。
 */
export function useBloggerPosts() {
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<HookState>({
    data: null,
    error: null,
    isLoading: true,
    isRefreshing: false,
    refreshNotice: null,
  });
  const seq = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    fetchBloggerPosts(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState((prev) => ({ ...prev, data, error: null, isLoading: false }));
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : '博主动态加载失败';
        setState((prev) => ({ ...prev, isLoading: false, error: message }));
      });
    return () => controller.abort();
  }, [reload]);

  const refresh = useCallback(() => {
    const current = ++seq.current;
    setState((prev) => ({ ...prev, isRefreshing: true, refreshNotice: null }));
    refreshBloggerPosts()
      .then((result) => {
        if (seq.current !== current) return;
        if (result.status === 'busy') {
          setState((prev) => ({ ...prev, isRefreshing: false, refreshNotice: '已有采集任务在执行，已加载其最新落盘数据。' }));
        } else if (result.status === 'timeout') {
          setState((prev) => ({ ...prev, isRefreshing: false, refreshNotice: result.detail || '采集超时，稍后可重试。' }));
        } else if (result.status === 'error') {
          setState((prev) => ({ ...prev, isRefreshing: false, refreshNotice: result.detail || '采集失败，稍后可重试。' }));
        } else {
          setState((prev) => ({ ...prev, isRefreshing: false, refreshNotice: null }));
        }
        setReload((value) => value + 1);
      })
      .catch((cause: unknown) => {
        if (seq.current !== current) return;
        const message = cause instanceof Error ? cause.message : '手动采集失败';
        setState((prev) => ({ ...prev, isRefreshing: false, refreshNotice: `${message}当前列表保留落盘数据。` }));
      });
  }, []);

  return { ...state, refresh };
}
