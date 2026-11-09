/** 机构净持仓（手，左轴）与大盘指数收盘价（点，右轴）双轴折线；缺失值保持断点。 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import type { EChartsType } from 'echarts/core';
import { LineChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type {
  InstitutionId,
  InstitutionMeta,
  InstitutionSeriesView,
  InstitutionVariety,
} from '../data/types';

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer]);

export const CHART_RANGES = [20, 60, 120] as const;
export type ChartRange = (typeof CHART_RANGES)[number];

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

interface MarketLine {
  code: string;
  name: string;
  close: (number | null)[];
}

interface Props {
  dates: string[];
  /** 沪深300 基准收盘（统计基准）；仅在 market_lines 缺失时作为回退单线 */
  closes: (number | null)[];
  /** 仅展示用的大盘对照序列（上证指数 + 沪深300 收盘价点位） */
  marketLines?: MarketLine[];
  institutions: InstitutionMeta[];
  series: Partial<Record<InstitutionId, Partial<Record<InstitutionVariety, InstitutionSeriesView>>>>;
  variety: InstitutionVariety;
  institution: InstitutionId | 'all';
  range: ChartRange;
  loading?: boolean;
}

export function InstitutionPositionChart({
  dates,
  closes,
  marketLines,
  institutions,
  series,
  variety,
  institution,
  range,
  loading = false,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsType | null>(null);

  const option = useMemo(() => {
    const start = Math.max(0, dates.length - range);
    const viewDates = dates.slice(start);
    const textColor = cssVar('--color-slate-500', '#64748b');
    const gridColor = cssVar('--color-slate-100', '#f1f5f9');
    const colors: Record<InstitutionId, string> = {
      guotai: cssVar('--color-brand-500', '#3a9a6a'),
      citic: cssVar('--color-ocean-400', '#3a90f5'),
      others: cssVar('--color-slate-400', '#94a3b8'),
    };
    const selected = institutions.filter((meta) => institution === 'all' || meta.id === institution);
    const positionSeries = selected.map((meta) => ({
      name: `${meta.name}净持仓`,
      type: 'line' as const,
      yAxisIndex: 0,
      data: (series[meta.id]?.[variety]?.net ?? []).slice(start),
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 1.8, color: colors[meta.id] },
      itemStyle: { color: colors[meta.id] },
    }));
    // 指数线取与三家机构色（brand/ocean/slate）不冲突的既有色：上证珊瑚橙、沪深300 薄荷青
    const indexColors: Record<string, string> = {
      sh000001: cssVar('--color-coral-500', '#f26522'),
      sh000300: cssVar('--color-mint-500', '#14b8a6'),
    };
    const fallbackIndexColor = cssVar('--color-coral-500', '#f26522');
    const lines: MarketLine[] = marketLines && marketLines.length > 0
      ? marketLines
      : [{ code: 'sh000300', name: '沪深300', close: closes }];
    const indexNames = new Set(lines.map((line) => line.name));
    const indexSeries = lines.map((line) => ({
      name: line.name,
      type: 'line' as const,
      yAxisIndex: 1,
      data: line.close.slice(start),
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 1.4, color: indexColors[line.code] ?? fallbackIndexColor },
      itemStyle: { color: indexColors[line.code] ?? fallbackIndexColor },
    }));

    return {
      animation: false,
      grid: { left: 12, right: 16, top: 44, bottom: 12, containLabel: true },
      legend: { top: 4, textStyle: { color: textColor, fontSize: 11 } },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', lineStyle: { color: textColor, width: 1 } },
        backgroundColor: 'rgba(255,255,255,0.96)',
        borderColor: gridColor,
        textStyle: { color: cssVar('--color-slate-700', '#334155'), fontSize: 12 },
        formatter: (params: { seriesName: string; marker: string; value: number | null; dataIndex: number }[]) => {
          const items = params.map((item) => {
            const suffix = indexNames.has(item.seriesName) ? ' 点' : ' 手';
            const value = item.value === null || item.value === undefined
              ? '数据缺失'
              : `${item.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`;
            return `${item.marker}${item.seriesName}：${value}`;
          });
          return [viewDates[params[0]?.dataIndex ?? 0], ...items].join('<br/>');
        },
      },
      xAxis: {
        type: 'category',
        data: viewDates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: gridColor } },
        axisLabel: { color: textColor, fontSize: 11, hideOverlap: true },
        axisTick: { show: false },
      },
      yAxis: [
        {
          type: 'value', name: '净持仓（手）', scale: true,
          nameTextStyle: { color: textColor, fontSize: 11 },
          axisLabel: { color: textColor, fontSize: 11 },
          splitLine: { lineStyle: { color: gridColor } },
        },
        {
          type: 'value', name: '指数点位', scale: true,
          nameTextStyle: { color: textColor, fontSize: 11 },
          axisLabel: { color: textColor, fontSize: 11 },
          splitLine: { show: false },
        },
      ],
      dataZoom: [{ type: 'inside', throttle: 60 }],
      series: [
        ...positionSeries,
        ...indexSeries,
      ],
    };
  }, [dates, closes, marketLines, institutions, series, variety, institution, range]);

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
      <div ref={containerRef} className="w-full h-80" />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/60 rounded-xl">
          <span className="text-sm text-slate-500">机构持仓加载中…</span>
        </div>
      )}
      {!loading && dates.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-sm text-slate-400">暂无机构持仓数据</span>
        </div>
      )}
    </div>
  );
}
