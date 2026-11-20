/**
 * 【宽基 ETF 申购净流入】图纯函数：列式序列切片、昨日/近60日统计、双轴柱线 option 构造。
 * 全部只读、确定性；时间范围由 ECharts dataZoom slider 在前端裁切，不改变任何原始数值。
 */
import type { EtfShareFlow } from '../data/types';

export interface ShareFlowView {
  dates: string[];
  /** 净流入，亿元 */
  inflowYi: number[];
  indexClose: (number | null)[];
}

/** 全量视图：列式序列原样映射，不裁切（裁切交给图内 slider）。 */
export function buildShareFlowView(series: EtfShareFlow): ShareFlowView {
  return {
    dates: [...series.dates],
    inflowYi: [...series.net_inflow_yi],
    indexClose: [...series.index_close],
  };
}

/** 近 N 个交易日净流入之和；不足 N 日按实际天数求和。 */
export function sumRecent(values: number[], days: number): number {
  const tail = values.slice(-days);
  return Math.round(tail.reduce((total, value) => total + value, 0) * 100) / 100;
}

/** 亿元文本：带正负号，一位小数。 */
export function formatSignedYi(value: number): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}`;
}

export interface ShareFlowPalette {
  /** 净流入（正）红，遵循 A 股涨红跌绿 */
  inflow: string;
  /** 净流出（负）绿 */
  outflow: string;
  index: string;
  text: string;
  grid: string;
}

/**
 * 净流入柱状 + 上证指数折线双轴 option，含可拖拽横向 dataZoom slider。
 * 柱色按每根自身正负逐点着色（红正绿负），指数缺失保持 null 断点。
 */
export function buildShareFlowOption(view: ShareFlowView, palette: ShareFlowPalette) {
  const bars = view.inflowYi.map((value) => ({
    value,
    itemStyle: { color: value >= 0 ? palette.inflow : palette.outflow },
  }));
  return {
    animation: false,
    grid: { left: 12, right: 16, top: 40, bottom: 66, containLabel: true },
    legend: { top: 4, textStyle: { color: palette.text, fontSize: 11 } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: palette.text, width: 1 } },
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: palette.grid,
      textStyle: { color: '#334155', fontSize: 12 },
      formatter: (params: { seriesName: string; marker: string; value: number | null; dataIndex: number }[]) => {
        const lines = params.map((item) => {
          const value = item.value === null || item.value === undefined
            ? '数据缺失'
            : item.seriesName === '上证指数'
              ? `${Number(item.value).toFixed(2)} 点`
              : `${formatSignedYi(Number(item.value))} 亿元`;
          return `${item.marker}${item.seriesName}：${value}`;
        });
        return [view.dates[params[0]?.dataIndex ?? 0], ...lines].join('<br/>');
      },
    },
    xAxis: {
      type: 'category',
      data: view.dates,
      axisLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.text, fontSize: 11, hideOverlap: true },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value', name: '净流入（亿元）', scale: true,
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
    // 截图式横向滑块：两端手柄可拖拽筛时间范围，与主图联动
    dataZoom: [
      { type: 'inside', xAxisIndex: 0, throttle: 60, filterMode: 'none' },
      {
        type: 'slider', xAxisIndex: 0, bottom: 8, height: 26,
        borderColor: palette.grid,
        fillerColor: 'rgba(58,154,106,0.18)',
        handleStyle: { color: '#3a9a6a', borderColor: '#2b6b49' },
        moveHandleStyle: { color: '#3a9a6a' },
        dataBackground: {
          // 未选中区间背景加深，浅灰底太浅看不清（2026-11-20 反馈，二次加深）
          lineStyle: { color: 'rgba(51,65,85,0.95)' },
          areaStyle: { color: 'rgba(71,85,105,0.8)' },
        },
        selectedDataBackground: {
          lineStyle: { color: '#3a9a6a' },
          areaStyle: { color: 'rgba(58,154,106,0.18)' },
        },
        textStyle: { color: palette.text, fontSize: 10 },
        filterMode: 'none',
      },
    ],
    series: [
      {
        name: '宽基ETF申购净流入',
        type: 'bar',
        yAxisIndex: 0,
        data: bars,
        barMaxWidth: 12,
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
