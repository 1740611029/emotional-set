/**
 * 【宽基 ETF 申购净流入·近 6 年】图：上层累计净流入折线（左轴）+ 上证指数折线（右轴），
 * 下层日频净流入折线（零轴居中），两块共用同一个横向 dataZoom 滑块。
 *
 * 与既有 `EtfShareFlowChart`（柱状图）完全独立：本组件不 import 也不改动既有图，
 * 两条数据线各自渲染、各自降级。
 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import type { EChartsCoreOption, EChartsType } from 'echarts/core';
import { LineChart } from 'echarts/charts';
import {
  DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import {
  buildBroadInflow6yOption,
  type BroadInflow6yView,
} from '../lib/broadInflow6y';
import { TimeAxisLabelRow, useTimeAxisLabels } from './chartTimeAxis';

echarts.use([
  LineChart, GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkLineComponent, CanvasRenderer,
]);

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

interface Props {
  view: BroadInflow6yView;
  loading?: boolean;
}

export function BroadInflow6yChart({ view, loading = false }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsType | null>(null);

  const option = useMemo(() => buildBroadInflow6yOption(view, {
    // 累计线用 A 股「资金流入」红，指数灰，日频中性色（正负由零轴上下位置表达，不靠颜色）
    cum: cssVar('--color-coral-500', '#f26522'),
    daily: cssVar('--color-slate-500', '#64748b'),
    index: cssVar('--color-slate-400', '#94a3b8'),
    text: cssVar('--color-slate-500', '#64748b'),
    grid: cssVar('--color-slate-100', '#f1f5f9'),
    zeroLine: cssVar('--color-slate-300', '#cbd5e1'),
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

  const [timeStart, timeEnd] = useTimeAxisLabels(chartRef, option);

  return (
    <div className="relative">
      <div ref={containerRef} className="w-full h-[460px]" />
      <TimeAxisLabelRow start={timeStart} end={timeEnd} />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/60 rounded-xl">
          <span className="text-sm text-slate-500">加载中…</span>
        </div>
      )}
    </div>
  );
}