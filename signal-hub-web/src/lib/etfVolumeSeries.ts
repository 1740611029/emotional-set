/**
 * 【ETF 放量】页纯函数：列式序列切片、元转亿元、双轴/多线 option 构造、L3 标记 datum。
 *
 * 全部只读、确定性：同一区间重复调用结果完全一致；不重新请求、不改变任何服务端数值，
 * 时间范围只裁剪已有序列。颜色由调用方（组件）传入既有 cssVar 值，本模块不硬编码主色。
 */
import type { EtfVolumeSeries } from '../data/types';
import { indexChartRangeOption, type IndexChartRangeKey } from './indexSignalCharts.ts';

/** 亿元换算：元 → 亿元，保留一位小数（数值型，供坐标与文本共用）。 */
export function toYi(amountYuan: number): number {
  return Math.round((amountYuan / 1e8) * 10) / 10;
}

/** 亿元文本，固定一位小数。 */
export function formatYi(amountYuan: number): string {
  return `${(amountYuan / 1e8).toFixed(1)}`;
}

/** L3 全池爆量的阈值口径（单日成交额 ≥ 100 亿元人民币）。 */
export const ETF_VOLUME_THRESHOLD_YI = 100;

export interface EtfSeriesView {
  dates: string[];
  /** 合计成交额，亿元一位小数 */
  totalYi: number[];
  /** 对齐的上证指数收盘点位，缺失保持 null */
  indexClose: (number | null)[];
  /** 6 只单只成交额，亿元一位小数，顺序与固化核心池一致 */
  etfs: { code: string; name: string; index: string; amountYi: number[] }[];
  /** 落在当前切片内的 L3 日期 */
  l3Dates: string[];
}

/** 按档位从后取 N 个交易日（不足取全部）；null 档取齐备区间全长。 */
function takeWindow(length: number, range: IndexChartRangeKey): number {
  const tradingDays = indexChartRangeOption(range).tradingDays;
  if (tradingDays === null) return length;
  return Math.min(tradingDays, length);
}

/** 切片视图：所有数组同步截取尾部同一段，保证逐日对齐。 */
export function sliceEtfSeriesToView(series: EtfVolumeSeries, range: IndexChartRangeKey): EtfSeriesView {
  const take = takeWindow(series.dates.length, range);
  const start = series.dates.length - take;
  const dates = series.dates.slice(start);
  const visible = new Set(dates);
  return {
    dates,
    totalYi: series.total_amount.slice(start).map(toYi),
    indexClose: series.index_close.slice(start),
    etfs: series.etfs.map((etf) => ({
      code: etf.code,
      name: etf.name,
      index: etf.index,
      amountYi: etf.amount.slice(start).map(toYi),
    })),
    // L3 只保留标记本身是 L3 且落在切片内的日期；级别口径由服务端产物唯一决定
    l3Dates: series.l3_dates.filter((date) => visible.has(date)),
  };
}

export interface L3MarkPointDatum {
  name: string;
  coord: [string, number];
  symbol: 'pin';
  symbolSize: number;
  itemStyle: { color: string; borderColor: string; borderWidth: number };
  /** 悬停文本：日期 / 口径 / 当日合计 / 阈值 */
  tooltipText: string;
}

/** L3 标记 datum：坐标落在该日合计成交额（亿元）真实取值上，不做避让偏移。 */
export function buildL3MarkPoints(
  view: EtfSeriesView,
  color: string,
): L3MarkPointDatum[] {
  return view.dates
    .map((date, index) => ({ date, index }))
    .filter(({ date }) => view.l3Dates.includes(date))
    .map(({ date, index }) => {
      const totalYi = view.totalYi[index];
      return {
        name: `etf-l3-${date}`,
        coord: [date, totalYi] as [string, number],
        symbol: 'pin' as const,
        symbolSize: 42,
        itemStyle: { color, borderColor: '#ffffff', borderWidth: 1 },
        tooltipText: [
          date,
          '全池爆量（6 只全部放量）',
          `当日合计成交额 ${totalYi.toFixed(1)} 亿元`,
          `阈值 单日成交额 ≥ ${ETF_VOLUME_THRESHOLD_YI} 亿元人民币`,
        ].join('<br/>'),
      };
    });
}

export interface TotalIndexPalette {
  total: string;
  index: string;
  l3: string;
  text: string;
  grid: string;
}

/** 第一张图：合计成交额（左轴亿元）+ 上证指数（右轴点位）双轴 + L3 markPoint。 */
export function buildTotalIndexOption(view: EtfSeriesView, palette: TotalIndexPalette) {
  const markData = buildL3MarkPoints(view, palette.l3);
  return {
    animation: false,
    grid: { left: 12, right: 16, top: 44, bottom: 12, containLabel: true },
    legend: { top: 4, textStyle: { color: palette.text, fontSize: 11 } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: palette.text, width: 1 } },
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: palette.grid,
      textStyle: { color: '#334155', fontSize: 12 },
      formatter: (params: { seriesName: string; marker: string; value: number | null; dataIndex: number }[]) => {
        const lines = params.map((item) => {
          const unit = item.seriesName === '上证指数' ? '点' : '亿元';
          const value = item.value === null || item.value === undefined
            ? '数据缺失'
            : `${Number(item.value).toFixed(1)} ${unit}`;
          return `${item.marker}${item.seriesName}：${value}`;
        });
        return [view.dates[params[0]?.dataIndex ?? 0], ...lines].join('<br/>');
      },
    },
    xAxis: {
      type: 'category',
      data: view.dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.text, fontSize: 11, hideOverlap: true },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value', name: '成交额（亿元）', scale: true,
        nameTextStyle: { color: palette.text, fontSize: 11 },
        axisLabel: { color: palette.text, fontSize: 11 },
        splitLine: { lineStyle: { color: palette.grid } },
      },
      {
        type: 'value', name: '上证指数（点）', scale: true,
        nameTextStyle: { color: palette.text, fontSize: 11 },
        axisLabel: { color: palette.text, fontSize: 11 },
        splitLine: { show: false },
      },
    ],
    dataZoom: [{ type: 'inside', throttle: 60 }],
    series: [
      {
        name: '合计成交额',
        type: 'line',
        yAxisIndex: 0,
        data: view.totalYi,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.8, color: palette.total },
        itemStyle: { color: palette.total },
        markPoint: { symbol: 'pin', data: markData },
      },
      {
        name: '上证指数',
        type: 'line',
        yAxisIndex: 1,
        data: view.indexClose,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.4, color: palette.index },
        itemStyle: { color: palette.index },
      },
    ],
  };
}

export interface EtfLinesPalette {
  text: string;
  grid: string;
  /** 与核心池顺序对应的 6 个颜色 */
  lines: string[];
}

/** 第二张图：6 只 ETF 单只成交额同轴多线，图例可点击隐藏。 */
export function buildEtfLinesOption(view: EtfSeriesView, palette: EtfLinesPalette) {
  return {
    animation: false,
    grid: { left: 12, right: 16, top: 44, bottom: 12, containLabel: true },
    legend: {
      top: 4,
      type: 'scroll',
      textStyle: { color: palette.text, fontSize: 11 },
      data: view.etfs.map((etf) => `${etf.name} ${etf.code}`),
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: palette.text, width: 1 } },
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: palette.grid,
      textStyle: { color: '#334155', fontSize: 12 },
      formatter: (params: { seriesName: string; marker: string; value: number | null; dataIndex: number }[]) => {
        const lines = params.map((item) => {
          const value = item.value === null || item.value === undefined
            ? '数据缺失'
            : `${Number(item.value).toFixed(1)} 亿元`;
          return `${item.marker}${item.seriesName}：${value}`;
        });
        return [view.dates[params[0]?.dataIndex ?? 0], ...lines].join('<br/>');
      },
    },
    xAxis: {
      type: 'category',
      data: view.dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.text, fontSize: 11, hideOverlap: true },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value', name: '成交额（亿元）', scale: true,
      nameTextStyle: { color: palette.text, fontSize: 11 },
      axisLabel: { color: palette.text, fontSize: 11 },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    dataZoom: [{ type: 'inside', throttle: 60 }],
    series: view.etfs.map((etf, position) => {
      const color = palette.lines[position % palette.lines.length];
      return {
        name: `${etf.name} ${etf.code}`,
        type: 'line' as const,
        data: etf.amountYi,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.4, color },
        itemStyle: { color },
      };
    }),
  };
}
