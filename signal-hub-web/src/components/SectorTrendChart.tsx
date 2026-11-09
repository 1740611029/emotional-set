/** 板块走势图：收盘折线 + MA60 + 四态背景色带 + 悬停读数。 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import type { EChartsType } from 'echarts/core';
import { LineChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { SectorState } from '../data/types';
import {
  STATE_COLORS,
  STATE_LABELS,
  compressStates,
  movingAverage,
} from '../lib/sectorStates';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, MarkAreaComponent, DataZoomComponent, CanvasRenderer]);

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function withAlpha(hex: string, alpha: number): string {
  const clean = hex.replace('#', '');
  if (clean.length !== 6) return hex;
  const red = parseInt(clean.slice(0, 2), 16);
  const green = parseInt(clean.slice(2, 4), 16);
  const blue = parseInt(clean.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

export interface SectorTrendChartProps {
  dates: string[];
  closes: (number | null)[];
  states: (SectorState | null)[];
  heightClass?: string;
  loading?: boolean;
}

export function SectorTrendChart({
  dates,
  closes,
  states,
  heightClass = 'h-72',
  loading = false,
}: SectorTrendChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsType | null>(null);

  const option = useMemo(() => {
    const palette = {
      bull: cssVar('--color-mint-500', STATE_COLORS.bull),
      rise: cssVar('--color-ocean-400', STATE_COLORS.rise),
      warn: cssVar('--color-coral-300', STATE_COLORS.warn),
      bear: cssVar('--color-coral-500', STATE_COLORS.bear),
    };
    const textSecondary = cssVar('--color-slate-500', '#64748b');
    const lineColor = cssVar('--color-brand-500', '#3a9a6a');
    const maColor = cssVar('--color-ocean-400', '#3a90f5');
    const gridColor = cssVar('--color-slate-100', '#f1f5f9');

    const segments = compressStates(dates, states);
    const marks = segments.map((segment) => [
      { xAxis: segment.from, itemStyle: { color: withAlpha(palette[segment.state], 0.12) } },
      { xAxis: segment.to },
    ]);
    const stateByDate = new Map<string, SectorState | null>();
    dates.forEach((date, index) => stateByDate.set(date, states[index] ?? null));

    return {
      animation: false,
      grid: { left: 8, right: 16, top: 40, bottom: 8, containLabel: true },
      legend: {
        show: true,
        top: 4,
        left: 8,
        icon: 'rect',
        itemWidth: 14,
        itemHeight: 3,
        itemGap: 16,
        textStyle: { color: textSecondary, fontSize: 11 },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'line', lineStyle: { color: textSecondary, width: 1 } },
        backgroundColor: 'rgba(255,255,255,0.96)',
        borderColor: gridColor,
        textStyle: { color: cssVar('--color-slate-700', '#334155'), fontSize: 12 },
        formatter: (params: { dataIndex: number }[]) => {
          const index = params?.[0]?.dataIndex ?? 0;
          const date = dates[index];
          const close = closes[index];
          const state = stateByDate.get(date) ?? null;
          const label = state ? STATE_LABELS[state] : '数据不足';
          return `${date}<br/>收盘 ${close === null ? '—' : close.toFixed(2)}<br/>状态 ${label}`;
        },
      },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: gridColor } },
        axisLabel: { color: textSecondary, fontSize: 11, hideOverlap: true },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: textSecondary, fontSize: 11 },
        splitLine: { lineStyle: { color: gridColor } },
      },
      dataZoom: [{ type: 'inside', throttle: 60 }],
      series: [
        {
          name: '收盘',
          type: 'line',
          data: closes,
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1.6, color: lineColor },
          itemStyle: { color: lineColor },
          areaStyle: { color: withAlpha(lineColor, 0.06) },
          markArea: { silent: true, data: marks },
        },
        {
          name: 'MA60（60日均线）',
          type: 'line',
          data: movingAverage(closes, 60),
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1, type: 'dashed', color: maColor },
          itemStyle: { color: maColor },
        },
      ],
    };
  }, [dates, closes, states]);

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
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  return (
    <div className="relative">
      <div ref={containerRef} className={`w-full ${heightClass}`} />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/60 rounded-xl">
          <span className="text-sm text-slate-500">走势加载中…</span>
        </div>
      )}
      {!loading && dates.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-sm text-slate-400">暂无走势数据</span>
        </div>
      )}
    </div>
  );
}
