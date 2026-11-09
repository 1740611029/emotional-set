import type {
  Candidate, CandidateLayers, PrecisionPivotAnchor, Signal, SignalKind,
} from '../data/types';

export type IndexChartRangeKey = '1m' | '3m' | '6m' | '1y' | '2y' | '3y' | 'all';

export interface IndexChartRangeOption {
  key: IndexChartRangeKey;
  label: string;
  tradingDays: number | null;
}

export const INDEX_CHART_RANGES: readonly IndexChartRangeOption[] = [
  { key: '1m', label: '近一个月', tradingDays: 22 },
  { key: '3m', label: '近三个月', tradingDays: 66 },
  { key: '6m', label: '近半年', tradingDays: 126 },
  { key: '1y', label: '近一年', tradingDays: 252 },
  { key: '2y', label: '近两年', tradingDays: 504 },
  { key: '3y', label: '近三年', tradingDays: 756 },
  { key: 'all', label: '全部', tradingDays: null },
];

export const DEFAULT_INDEX_CHART_RANGE_KEY: IndexChartRangeKey = '1y';

export function indexChartRangeOption(key: IndexChartRangeKey): IndexChartRangeOption {
  const option = INDEX_CHART_RANGES.find((item) => item.key === key);
  if (!option) throw new Error(`未知指数图表时间范围：${key}`);
  return option;
}

export function sliceIndexHistory<T extends { date: string }>(
  history: T[],
  range: IndexChartRangeKey,
  endDate?: string,
): T[] {
  if (history.length === 0) return [];
  const effectiveEndDate = endDate ?? history[history.length - 1].date;
  const eligible = history.filter((row) => row.date <= effectiveEndDate);
  const option = indexChartRangeOption(range);
  if (option.tradingDays === null) return eligible;
  return eligible.slice(-option.tradingDays);
}

export type ChartSignalStatus = 'confirmed' | 'candidate';

export interface ChartSignalMarker {
  status: ChartSignalStatus;
  direction: SignalKind;
  date: string;
  price: number;
  layers?: CandidateLayers;
  tradingDaysSince?: number;
}

export function buildIndexSignalMarkers(
  signals: Signal[],
  candidates: Candidate[],
  visibleDates: string[],
): ChartSignalMarker[] {
  const visible = new Set(visibleDates);
  const confirmed = signals
    .filter((signal) => visible.has(signal.date))
    .map((signal) => ({
      status: 'confirmed' as const,
      direction: signal.type,
      date: signal.date,
      price: signal.price,
    }));
  const pending = candidates
    .filter((candidate) => visible.has(candidate.date))
    .map((candidate) => ({
      status: 'candidate' as const,
      direction: candidate.type,
      date: candidate.date,
      price: candidate.price,
      layers: candidate.layers,
      tradingDaysSince: candidate.trading_days_since,
    }));
  return [...confirmed, ...pending].sort((left, right) => left.date.localeCompare(right.date));
}

export function formatCandidateLayers(layers: CandidateLayers): string {
  return [
    `价格 ${layers.price ? '通过' : '未通过'}`,
    `动量 ${layers.momentum ? '通过' : '未通过'}`,
    `量能 ${layers.volume ? '通过' : '未通过'}`,
  ].join(' · ');
}

export function formatIndexSignalMarkerTooltip(marker: ChartSignalMarker, provisional = false): string {
  const status = marker.status === 'confirmed'
    ? provisional ? '盘中临时（未确认）' : '已确认'
    : '待确认';
  const direction = marker.direction === 'bottom' ? '底部' : '顶部';
  const lines = [
    marker.date,
    `状态 ${status}`,
    `方向 ${direction}`,
    `触发价 ${marker.price.toLocaleString('zh-CN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`,
  ];
  if (marker.status === 'candidate') {
    if (marker.layers) lines.push(`确认进度 ${formatCandidateLayers(marker.layers)}`);
    if (marker.tradingDaysSince !== undefined) lines.push(`已等待 ${marker.tradingDaysSince} 个交易日`);
  }
  return lines.join('<br/>');
}

export interface IndexSignalChartPalette {
  line: string;
  bottom: string;
  top: string;
  text: string;
  grid: string;
  /** 拐点基准日（事后标注）用天蓝 */
  pivot: string;
  /** 拐点自然确认日用品牌绿 */
  natural: string;
}

export const DEFAULT_INDEX_SIGNAL_CHART_PALETTE: IndexSignalChartPalette = {
  line: '#3a9a6a',
  bottom: '#14b8a6',
  top: '#f26522',
  text: '#64748b',
  grid: '#f1f5f9',
  pivot: '#7dd3fc',
  natural: '#3a9a6a',
};

export interface ChartMarkerDatum {
  name: string;
  coord: [string, number];
  symbol: 'triangle' | 'circle' | 'diamond' | 'rect';
  /** 仅信号标记使用：顶部朝下为 180 */
  symbolRotate?: 0 | 180;
  symbolSize: number;
  itemStyle: {
    color: string;
    borderColor: string;
    borderWidth: number;
  };
  /** 信号标记信息 */
  markerInfo?: ChartSignalMarker;
}

export interface IndexChartTooltipParam {
  seriesType?: string;
  dataIndex?: number;
  axisValue?: string;
  data?: {
    markerInfo?: ChartSignalMarker;
  };
}

export interface BuiltIndexSignalChartOption {
  animation: false;
  grid: { left: number; right: number; top: number; bottom: number; containLabel: boolean };
  tooltip: {
    trigger: 'axis';
    backgroundColor: string;
    borderColor: string;
    textStyle: { color: string; fontSize: number };
    formatter: (params: IndexChartTooltipParam | IndexChartTooltipParam[]) => string;
  };
  xAxis: {
    type: 'category';
    data: string[];
    boundaryGap: false;
    axisLine: { lineStyle: { color: string } };
    axisLabel: { color: string; fontSize: number; hideOverlap: boolean };
    axisTick: { show: boolean };
  };
  yAxis: {
    type: 'value';
    scale: true;
    axisLabel: { color: string; fontSize: number };
    splitLine: { lineStyle: { color: string } };
  };
  series: [
    {
      name: '收盘价';
      type: 'line';
      data: (number | null)[];
      showSymbol: boolean;
      connectNulls: false;
      lineStyle: { width: number; color: string };
      itemStyle: { color: string };
      markPoint: {
        symbol: 'circle';
        data: ChartMarkerDatum[];
      };
    },
  ];
}

export function buildIndexSignalChartOption(
  dates: string[],
  closes: (number | null)[],
  markers: ChartSignalMarker[],
  provisional = false,
  palette: IndexSignalChartPalette = DEFAULT_INDEX_SIGNAL_CHART_PALETTE,
  pivotAnchors: PrecisionPivotAnchor[] = [],
): BuiltIndexSignalChartOption {
  const markerData: ChartMarkerDatum[] = markers.map((marker) => {
    const color = marker.direction === 'bottom' ? palette.bottom : palette.top;
    return {
      name: `${marker.direction}-${marker.date}`,
      coord: [marker.date, marker.price],
      symbol: 'triangle',
      symbolRotate: marker.direction === 'top' ? 180 : 0,
      symbolSize: marker.status === 'candidate' ? 9 : 11,
      itemStyle: {
        color: marker.status === 'candidate' ? '#ffffff' : color,
        borderColor: color,
        borderWidth: marker.status === 'candidate' ? 2 : 0,
      },
      markerInfo: marker,
    };
  });

  // 三个日期三种样式：◆ 事后标注的拐点基准日、■ 拐点自然确认日、▲ 信号确认日。
  const visible = new Set(dates);
  const closeAt = (day: string): number | null => {
    const position = dates.indexOf(day);
    if (position < 0) return null;
    const close = closes[position];
    return close === null || close === undefined ? null : close;
  };
  for (const anchor of pivotAnchors) {
    if (visible.has(anchor.pivot_date)) {
      markerData.push({
        name: `pivot-base-${anchor.pivot_date}`,
        coord: [anchor.pivot_date, anchor.pivot_price],
        symbol: 'diamond',
        symbolSize: 10,
        itemStyle: { color: '#ffffff', borderColor: palette.pivot, borderWidth: 2 },
      });
    }
    const naturalClose = closeAt(anchor.natural_confirm_date);
    if (visible.has(anchor.natural_confirm_date) && naturalClose !== null) {
      markerData.push({
        name: `pivot-natural-${anchor.natural_confirm_date}`,
        coord: [anchor.natural_confirm_date, naturalClose],
        symbol: 'rect',
        symbolSize: 9,
        itemStyle: { color: palette.natural, borderColor: '#ffffff', borderWidth: 1 },
      });
    }
  }

  const pivotDetails = (date: string): string[] => {
    const lines: string[] = [];
    for (const anchor of pivotAnchors) {
      if (anchor.pivot_date === date) {
        lines.push('◆ 拐点基准日：事后标注，不代表信号发出的时点');
        lines.push(`拐点价 ${anchor.pivot_price.toFixed(2)}`);
      }
      if (anchor.natural_confirm_date === date) {
        lines.push('■ 拐点自然确认日：到这一天拐点才变得可知');
      }
      if (anchor.date === date) {
        lines.push(`▲ 信号确认日，相对拐点 ${anchor.signal_confirm_offset_from_pivot} 个交易日`);
      }
    }
    return lines;
  };

  const dayDetails = (date: string) => [
    ...markers
      .filter((marker) => marker.date === date)
      .map((marker) => formatIndexSignalMarkerTooltip(marker, provisional)),
    ...pivotDetails(date),
  ];

  return {
    animation: false,
    grid: { left: 8, right: 16, top: 18, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: palette.grid,
      textStyle: { color: '#334155', fontSize: 12 },
      formatter: (params) => {
        if (Array.isArray(params)) {
          const lineParam = params.find((param) => param.seriesType === 'line') ?? params[0];
          if (!lineParam) return '--';
          const index = lineParam.dataIndex ?? 0;
          const date = dates[index] ?? lineParam.axisValue ?? '--';
          const close = closes[index];
          const sections = [
            `${date}<br/>收盘 ${close === null || close === undefined ? '—' : close.toFixed(2)}`,
          ];
          sections.push(...dayDetails(date));
          return sections.join('<br/>');
        }
        if (params.data?.markerInfo) {
          return formatIndexSignalMarkerTooltip(params.data.markerInfo, provisional);
        }
        if (params.seriesType === 'line') {
          const index = params.dataIndex ?? 0;
          const close = closes[index];
          const date = dates[index] ?? params.axisValue ?? '--';
          const headline = `${date}<br/>收盘 ${close === null || close === undefined ? '—' : close.toFixed(2)}`;
          const details = dayDetails(date);
          return details.length > 0 ? [headline, ...details].join('<br/>') : headline;
        }
        return '--';
      },
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.text, fontSize: 11, hideOverlap: true },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: palette.text, fontSize: 11 },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    series: [
      {
        name: '收盘价',
        type: 'line',
        data: closes,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.5, color: palette.line },
        itemStyle: { color: palette.line },
        markPoint: {
          symbol: 'circle',
          data: markerData,
        },
      },
    ],
  };
}
