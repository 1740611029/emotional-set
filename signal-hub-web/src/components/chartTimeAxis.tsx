/**
 * 图表横向时间滑块（dataZoom slider）的常显时间范围标签。
 *
 * ECharts slider 两端手柄的日期标签默认只在悬停时显示，无法原生常显；
 * 这里通过监听 dataZoom 事件读取当前可见区间，把起止日期渲染在图表下方，
 * 不悬停也始终可见。仅读取展示信息，不改动任何图表数值。
 */
import { useEffect, useState, type RefObject } from 'react';
import type { EChartsType } from 'echarts/core';

/** 从 option 中读取 category xAxis 的日期数组（兼容单对象与数组两种写法）。 */
function readXAxisDates(option: unknown): string[] {
  const xAxis = (option as { xAxis?: unknown } | undefined)?.xAxis;
  if (Array.isArray(xAxis)) {
    return (xAxis[0] as { data?: string[] } | undefined)?.data ?? [];
  }
  if (xAxis && typeof xAxis === 'object') {
    return (xAxis as { data?: string[] }).data ?? [];
  }
  return [];
}

interface DataZoomSnapshot {
  type?: string;
  start?: number;
  end?: number;
  startValue?: number | string;
  endValue?: number | string;
}

/** 监听 dataZoom 变化，返回当前可见区间 [起始日期, 结束日期]；无数据时为空串。 */
export function useTimeAxisLabels(
  chartRef: RefObject<EChartsType | null>,
  option: unknown,
): [string, string] {
  const [range, setRange] = useState<[string, string]>(['', '']);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return undefined;
    let cancelled = false;

    const update = () => {
      if (cancelled) return;
      const dates = readXAxisDates(option);
      if (dates.length === 0) {
        setRange(['', '']);
        return;
      }
      const last = dates.length - 1;
      const opt = chart.getOption() as { dataZoom?: DataZoomSnapshot[] };
      const dz = opt.dataZoom?.find((item) => item.type === 'slider') ?? opt.dataZoom?.[0];
      let startIndex = 0;
      let endIndex = last;
      if (dz) {
        if (typeof dz.startValue === 'number' && typeof dz.endValue === 'number') {
          startIndex = dz.startValue;
          endIndex = dz.endValue;
        } else {
          startIndex = Math.round(((dz.start ?? 0) / 100) * last);
          endIndex = Math.round(((dz.end ?? 100) / 100) * last);
        }
      }
      startIndex = Math.max(0, Math.min(last, Math.round(startIndex)));
      endIndex = Math.max(0, Math.min(last, Math.round(endIndex)));
      setRange([dates[startIndex], dates[endIndex]]);
    };

    update();
    const handler = () => {
      requestAnimationFrame(update);
    };
    chart.on('datazoom', handler);
    return () => {
      cancelled = true;
      chart.off('datazoom', handler);
    };
  }, [chartRef, option]);

  return range;
}

/** 图表下方的常显时间范围行：左端起始日期、右端结束日期。 */
export function TimeAxisLabelRow({ start, end }: { start: string; end: string }) {
  return (
    <div className="flex justify-between items-center mt-1 px-1 text-xs text-slate-400" aria-label="当前时间范围">
      <span>{start}</span>
      <span>{end}</span>
    </div>
  );
}
