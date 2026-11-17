import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentType, ReactNode, SVGProps } from 'react';

/** 页面头部元信息：由各路由页面注册，TopBar 统一渲染（需求 2026-11-17：内容区标题块上移到顶部栏） */

export interface PageMetaStat {
  label: string;
  value: string;
}

export interface PageMetaRefresh {
  label: string;
  busyLabel: string;
  busy: boolean;
  onRefresh: () => void | Promise<void>;
}

export interface PageMeta {
  /** 页面图标，缺省不显示图标 */
  icon?: ComponentType<SVGProps<SVGSVGElement>>;
  /** 图标渐变背景（跟随项目渐变规范） */
  gradientClass?: string;
  /** 副标题/描述（第二行小字） */
  subtitle?: string;
  /** 第一行日期位置展示：text 为展示文本，warn=true 显示珊瑚色警示（如滞后提示） */
  asOf?: { text: string; warn?: boolean } | null;
  /** 第二行附加键值统计（如最近更新、累计收录）；缺省不显示 */
  stats?: PageMetaStat[];
  /** 页面级刷新按钮，仅在存在时渲染 */
  refresh?: PageMetaRefresh;
}

interface PageMetaContextValue {
  meta: PageMeta | null;
  setPageMeta: (meta: PageMeta | null) => void;
}

const PageMetaContext = createContext<PageMetaContextValue>({
  meta: null,
  setPageMeta: () => {},
});

export function PageMetaProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<PageMeta | null>(null);
  const value = useMemo(() => ({ meta, setPageMeta: setMeta }), [meta]);
  return <PageMetaContext.Provider value={value}>{children}</PageMetaContext.Provider>;
}

export function usePageMetaContext(): PageMetaContextValue {
  return useContext(PageMetaContext);
}

/**
 * 页面注册自身头部元信息。
 * 仅当 meta 的可序列化部分变化时才更新顶层状态，避免每次渲染重复触发；
 * 刷新函数通过 ref 取最新值，保证拿到当页最新闭包。
 */
export function usePageMeta(meta: PageMeta) {
  const { setPageMeta } = usePageMetaContext();
  const refreshRef = useRef(meta.refresh);
  refreshRef.current = meta.refresh;
  // 可序列化 key：包含刷新按钮的展示态（label/busy），不含函数本身
  const refreshLite = meta.refresh
    ? { label: meta.refresh.label, busyLabel: meta.refresh.busyLabel, busy: meta.refresh.busy }
    : null;
  const { refresh: _omit, ...rest } = meta;
  const key = JSON.stringify({ ...rest, refreshLite });
  useEffect(() => {
    setPageMeta({
      ...rest,
      refresh: refreshLite
        ? { ...refreshLite, onRefresh: () => void refreshRef.current?.onRefresh() }
        : undefined,
    });
    return () => setPageMeta(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}
