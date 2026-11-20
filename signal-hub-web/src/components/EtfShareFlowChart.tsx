/** 【宽基 ETF 申购净流入】图：净流入红绿柱（左轴）+ 上证指数折线（右轴）+ 横向时间滑块。 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import type { EChartsCoreOption, EChartsType } from 'echarts/core';
import { BarChart, LineChart } from 'echarts/charts';
import {
  DataZoomComponent, GridComponent, LegendComponent, TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import {
  buildShareFlowOption,
  type ShareFlowView,
} from '../lib/etfShareFlow.ts';

echarts.use([
  BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, CanvasRenderer,
]);

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

interface Props {
  view: ShareFlowView;
  loading?: boolean;
}

export function EtfShareFlowChart({ view, loading = false }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsType | null>(null);

  const option = useMemo(() => buildShareFlowOption(view, {
    // A 股涨红跌绿：净流入红、净流出绿；指数用品牌绿以区别于柱体
    inflow: cssVar('--color-coral-500', '#f26522'),
    outflow: cssVar('--color-brand-500', '#3a9a6a'),
    index: cssVar('--color-slate-400', '#94a3b8'),
    text: cssVar('--color-slate-500', '#64748b'),
    grid: cssVar('--color-slate-100', '#f1f5f9'),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [view]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const chart = echarts.init(element, undefined, { renderer: 'canvas' });
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option as unknown as EChartsCoreOption, { notMerge: true });
  }, [option]);

  return (
    <div className="relative">
      <div ref={containerRef} className="w-full h-96" />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/60 rounded-xl">
          <span className="text-sm text-slate-500">加载中…</span>
        </div>
      )}
      {!loading && view.dates.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-sm text-slate-400">所选区间暂无齐备数据</span>
        </div>
      )}
    </div>
  );
}
