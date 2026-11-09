import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import type { EChartsCoreOption, EChartsType } from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, MarkPointComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { IndexItem, PrecisionPivotAnchor } from '../data/types';
import {
  buildIndexSignalChartOption,
  buildIndexSignalMarkers,
  sliceIndexHistory,
  type BuiltIndexSignalChartOption,
  type IndexChartRangeKey,
  type IndexSignalChartPalette,
} from '../lib/indexSignalCharts';
import { ChangeText } from './ui';

echarts.use([LineChart, GridComponent, TooltipComponent, MarkPointComponent, CanvasRenderer]);

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function chartPalette(): IndexSignalChartPalette {
  return {
    line: cssVar('--color-brand-500', '#3a9a6a'),
    bottom: cssVar('--color-mint-500', '#14b8a6'),
    top: cssVar('--color-coral-500', '#f26522'),
    text: cssVar('--color-slate-500', '#64748b'),
    grid: cssVar('--color-slate-100', '#f1f5f9'),
    pivot: cssVar('--color-ocean-400', '#7dd3fc'),
    natural: cssVar('--color-brand-500', '#3a9a6a'),
  };
}

function IndexSignalChartCanvas({
  option,
  loading = false,
  empty = false,
}: {
  option: BuiltIndexSignalChartOption;
  loading?: boolean;
  empty?: boolean;
}) {
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
    <div className="relative mt-3">
      <div ref={containerRef} className="w-full h-56" />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/60 rounded-xl">
          <span className="text-sm text-slate-500">图表加载中…</span>
        </div>
      )}
      {!loading && empty && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-sm text-slate-400">暂无走势数据</span>
        </div>
      )}
    </div>
  );
}

export function IndexSignalChartCard({
  item,
  range,
  endDate,
  anchors = [],
  loading = false,
  className = '',
}: {
  item: IndexItem;
  range: IndexChartRangeKey;
  endDate?: string;
  anchors?: PrecisionPivotAnchor[];
  loading?: boolean;
  className?: string;
}) {
  const view = useMemo(
    () => sliceIndexHistory(item.history, range, endDate),
    [item.history, range, endDate],
  );
  const dates = useMemo(() => view.map((row) => row.date), [view]);
  const closes = useMemo(() => view.map((row) => row.close), [view]);
  const markers = useMemo(
    () => buildIndexSignalMarkers(item.signals, item.candidates, dates),
    [item.signals, item.candidates, dates],
  );
  const option = useMemo(
    () => buildIndexSignalChartOption(
      dates, closes, markers, item.provisional, chartPalette(), anchors,
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dates, closes, markers, item.provisional, anchors],
  );
  const coverage = dates.length > 0
    ? `${dates[0]} ~ ${dates[dates.length - 1]} · ${dates.length} 个交易日`
    : '暂无走势数据';

  return (
    <section className={`glass-card rounded-2xl p-5 ${className}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-bold text-slate-800">{item.name}</h3>
            {item.provisional && (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-500">
                盘中临时
              </span>
            )}
          </div>
          <p className="mt-1 text-[11px] text-slate-400 tabular-nums">{coverage}</p>
        </div>
        <div className="text-right">
          <p className="text-lg font-bold text-slate-800 tabular-nums">
            {item.latest.toLocaleString('zh-CN', {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
          </p>
          <ChangeText value={item.change_pct} className="text-xs" />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <span><span className="text-mint-600">▲</span> 信号确认日（底部）</span>
        <span><span className="text-coral-500">▼</span> 信号确认日（顶部）</span>
        <span><span className="text-brand-600">△</span> 候选（未确认）</span>
        <span><span className="text-ocean-500">◆</span> 拐点基准日（事后标注）</span>
        <span><span className="text-brand-600">■</span> 拐点自然确认日</span>
      </div>
      {anchors.length > 0 && (
        <p className="mt-1 text-[11px] text-slate-400">
          三种日期样式不同：◆ 是事后来回看才标出的拐点，不代表信号发出的时点；■ 是拐点变得可知的日子；▲/▼ 才是真正发信号的确认日。
        </p>
      )}

      <IndexSignalChartCanvas option={option} loading={loading} empty={dates.length === 0} />
    </section>
  );
}
