/** 连板个股日线弹窗：日 K 线（红涨绿跌）+ 成交量，数据来自 /api/stock/daily/{code}。 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import * as echarts from 'echarts/core';
import type { EChartsCoreOption, EChartsType } from 'echarts/core';
import { BarChart, CandlestickChart } from 'echarts/charts';
import {
  DataZoomComponent, GridComponent, TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Notice } from './ui';
import { fetchStockDaily, type StockDailyPayload, type StockDailyPoint } from '../data/stockDaily';

echarts.use([
  BarChart, CandlestickChart, GridComponent, TooltipComponent,
  DataZoomComponent, CanvasRenderer,
]);

const UP = '#c0392b';   // A 股习惯：涨红
const DOWN = '#3a9a6a'; // 跌绿，与项目品牌绿一致
const CHART_HEIGHT = 'min(440px, 55vh)'; // 小窗口下收缩，避免弹窗超出视口被裁

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char] ?? char);
}

function buildOption(points: StockDailyPoint[]): EChartsCoreOption {
  const dates = points.map((point) => point.date);
  const candles = points.map((point) => [point.open, point.close, point.low, point.high]);
  const volumes = points.map((point) => ({
    value: point.volume ?? 0,
    itemStyle: { color: point.close >= point.open ? UP : DOWN },
  }));

  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', label: { backgroundColor: '#64748b' } },
      backgroundColor: 'rgba(255,255,255,0.97)',
      borderColor: '#e2e8f0',
      borderWidth: 1,
      textStyle: { color: '#334155', fontSize: 12 },
      formatter: (raw: unknown) => {
        const list = Array.isArray(raw) ? raw : [raw];
        const first = list[0] as { dataIndex?: number } | undefined;
        const point = points[first?.dataIndex ?? 0];
        if (!point) return '';
        const up = point.close >= point.open;
        const pct = point.change_pct;
        return [
          `<div style="color:#64748b;margin-bottom:4px">${escapeHtml(point.date)}</div>`,
          `<div>开盘 <b>${point.open}</b>　收盘 <b style="color:${up ? UP : DOWN}">${point.close}</b></div>`,
          `<div>最高 <b>${point.high}</b>　最低 <b>${point.low}</b></div>`,
          `<div>涨跌幅 <b style="color:${pct != null && pct >= 0 ? UP : DOWN}">${pct != null ? `${pct.toFixed(2)}%` : '—'}</b></div>`,
          `<div>成交量（股） <b>${point.volume != null ? point.volume.toLocaleString('zh-CN') : '—'}</b></div>`,
        ].join('');
      },
    },
    grid: [
      { left: 56, right: 20, top: 16, height: '56%' },
      { left: 56, right: 20, top: '78%', height: '12%' },
    ],
    xAxis: [
      {
        type: 'category', data: dates, gridIndex: 0, boundaryGap: true,
        axisTick: { show: false }, axisLabel: { show: false },
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        splitLine: { show: false },
      },
      {
        type: 'category', data: dates, gridIndex: 1, boundaryGap: true,
        axisTick: { show: false },
        axisLabel: { color: '#94a3b8', fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true, gridIndex: 0, position: 'left',
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
      },
      {
        scale: true, gridIndex: 1, position: 'left', splitNumber: 2,
        axisLabel: { show: false }, splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 50, end: 100 },
      {
        type: 'slider', xAxisIndex: [0, 1], bottom: 4, height: 14,
        borderColor: '#e2e8f0', fillerColor: 'rgba(58,154,106,0.12)',
        handleStyle: { color: '#3a9a6a' },
        textStyle: { color: '#94a3b8', fontSize: 10 },
      },
    ],
    series: [
      {
        name: '日K', type: 'candlestick', data: candles,
        xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: {
          color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN,
        },
      },
      {
        name: '成交量', type: 'bar', data: volumes,
        xAxisIndex: 1, yAxisIndex: 1, barWidth: '60%',
      },
    ],
  } as unknown as EChartsCoreOption;
}

function StockKlineChart({ points }: { points: StockDailyPoint[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsType | null>(null);
  const option = useMemo(() => buildOption(points), [points]);

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

  return <div ref={containerRef} className="w-full" style={{ height: CHART_HEIGHT }} />;
}

export function StockDailyDialog({ code, name, boards, onClose }: {
  code: string;
  name: string;
  boards: number;
  onClose: () => void;
}) {
  const [data, setData] = useState<StockDailyPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);
    fetchStockDaily(code, controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) return;
        setData(payload);
        if (payload.data_state === 'error') setError(payload.error ?? '日线数据不可用');
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : '个股日线加载失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [code]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  // 用 Portal 挂到 document.body：脱离 .fade-in（含 transform）等祖先的 containing block，
  // 使 fixed 相对浏览器视口定位；否则页面滚动时弹窗会跟着内容容器一起移动、被裁掉一半。
  return createPortal(
    <div
      className="fixed inset-0 z-50 overflow-y-auto bg-slate-900/40"
      onClick={onClose}
      role="presentation"
    >
      <div className="flex min-h-full items-center justify-center p-4">
        <div
          className="w-full max-w-4xl rounded-2xl bg-white shadow-xl overflow-hidden max-h-[calc(100vh-2rem)] overflow-y-auto"
          onClick={(event) => event.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label={`${name} 日线走势`}
        >
          <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
            <div>
              <h3 className="text-base font-bold text-slate-800">
                {name} <span className="ml-1 text-sm font-normal text-slate-400 tabular-nums">{code}</span>
              </h3>
              <p className="mt-1 text-xs text-slate-400">
                {boards === 1 ? '首板' : `${boards} 板`} · 日 K 线 + 成交量 · 新浪财经不复权 · 近 60 个交易日
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="shrink-0 rounded-lg px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-100 hover:text-slate-700"
              aria-label="关闭弹窗"
            >
              关闭
            </button>
          </header>

          <div className="px-3 py-3">
            {loading && (
              <div className="flex items-center justify-center text-sm text-slate-500" style={{ height: CHART_HEIGHT }}>
                正在加载日线数据…
              </div>
            )}
            {!loading && error && (
              <div className="flex items-center justify-center px-8" style={{ height: CHART_HEIGHT }}>
                <div className="w-full max-w-lg">
                  <Notice tone="warn">{error}</Notice>
                  <p className="mt-3 text-center text-xs text-slate-400">
                    数据拉取失败时不展示任何演示数值，可关闭后重试。
                  </p>
                </div>
              </div>
            )}
            {!loading && !error && data?.data_state === 'ready' && (
              <StockKlineChart points={data.points} />
            )}
          </div>

          <footer className="border-t border-slate-100 px-6 py-3 text-xs text-slate-400">
            {data?.as_of ? `截至 ${data.as_of} · ` : ''}
            仅含已发生交易日，不做预测；本图仅供展示，不参与任何信号计算。
          </footer>
        </div>
      </div>
    </div>,
    document.body,
  );
}
