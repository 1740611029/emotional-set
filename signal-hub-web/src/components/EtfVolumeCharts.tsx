/** 【ETF 放量】页两张图：合计成交额+上证指数双轴（含 L3 标记）、6 只 ETF 同轴多线。 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import type { EChartsCoreOption, EChartsType } from 'echarts/core';
import { LineChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import {
  buildEtfLinesOption,
  buildTotalIndexOption,
  type EtfSeriesView,
} from '../lib/etfVolumeSeries.ts';

echarts.use([
  LineChart, GridComponent, TooltipComponent, LegendComponent,
  MarkPointComponent, DataZoomComponent, CanvasRenderer,
]);

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** 单张图通用挂载：init + ResizeObserver + notMerge，容器 h-80，与机构页一致。 */
function useChart(option: object, loading: boolean, empty: boolean, emptyText: string) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsType | null>(null);

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
      <div ref={containerRef} className="w-full h-80" />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/60 rounded-xl">
          <span className="text-sm text-slate-500">加载中…</span>
        </div>
      )}
      {!loading && empty && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-sm text-slate-400">{emptyText}</span>
        </div>
      )}
    </div>
  );
}

interface ChartProps {
  view: EtfSeriesView;
  loading?: boolean;
}

/** 第一张图：合计成交额（左轴亿元）+ 上证指数（右轴点位），标 L3 全池爆量日。 */
export function TotalVolumeIndexChart({ view, loading = false }: ChartProps) {
  const option = useMemo(() => buildTotalIndexOption(view, {
    total: cssVar('--color-brand-500', '#3a9a6a'),
    index: cssVar('--color-coral-500', '#f26522'),
    l3: cssVar('--color-brand-700', '#2b6b49'),
    text: cssVar('--color-slate-500', '#64748b'),
    grid: cssVar('--color-slate-100', '#f1f5f9'),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [view]);
  return useChart(option, loading, view.dates.length === 0, '所选区间暂无齐备数据');
}

/** 第二张图：6 只 ETF 单只成交额同轴多线，图例可点击隐藏。 */
export function EtfAmountLinesChart({ view, loading = false }: ChartProps) {
  const option = useMemo(() => buildEtfLinesOption(view, {
    text: cssVar('--color-slate-500', '#64748b'),
    grid: cssVar('--color-slate-100', '#f1f5f9'),
    lines: [
      cssVar('--color-brand-500', '#3a9a6a'),
      cssVar('--color-ocean-400', '#3a90f5'),
      cssVar('--color-mint-500', '#14b8a6'),
      cssVar('--color-coral-500', '#f26522'),
      cssVar('--color-brand-700', '#2b6b49'),
      cssVar('--color-ocean-600', '#2563c9'),
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [view]);
  return useChart(option, loading, view.dates.length === 0, '所选区间暂无齐备数据');
}
