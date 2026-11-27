/**
 * 【宽基 ETF 申购净流入·近 6 年】图纯函数：双面板折线 option 构造。
 *
 * 为什么拆两块面板：累计净流入是万亿级（约 15000-20000 亿），日频净流入单日只有 ±500 亿，
 * 比值约 40:1。两者共用一根纵轴会把日频压成贴零轴的直线，等于没画。
 * 上层主图 = 累计净流入（左轴）+ 上证指数（右轴）；下层副图 = 日频净流入（零轴居中）。
 * 全部只读、确定性；时间范围由两块共用的 dataZoom 在前端裁切，不改变任何原始数值。
 */

/** 与后端 `server/broad_inflow_6y.py` 一一对应的列式产物结构。 */
export interface BroadInflow6y {
  data_state: 'ready' | 'preparing' | 'degraded';
  as_of: string | null;
  generated_at: string | null;
  stale?: boolean;
  stale_reason?: string | null;
  coverage: {
    start: string | null; end: string | null; trading_days: number;
    codes_seen: number; min_pool: number; max_pool: number;
    min_counted: number; max_counted: number;
  };
  dates: string[];
  /** 日频净流入，亿元 */
  daily_inflow_yi: number[];
  /** 累计净流入，亿元（从区间首日累加） */
  cum_inflow_yi: number[];
  index_close: (number | null)[];
  /** 当日宽基池只数（随市场扩张从约 25 只增至约 130 只） */
  etf_count: number[];
  stats: {
    cum_yi: number; last_day_yi: number | null;
    last_60d_yi: number; last_60d_days: number;
  };
  anomalies: { split_skipped: number; nav_missing: number };
  sources: { share?: string; nav?: string; pool?: string };
}

export interface BroadInflow6yView {
  dates: string[];
  dailyYi: number[];
  cumYi: number[];
  indexClose: (number | null)[];
  etfCount: number[];
}

/** 全量视图：列式序列原样映射，不裁切（裁切交给图内 slider）。 */
export function buildBroadInflow6yView(series: BroadInflow6y): BroadInflow6yView {
  return {
    dates: [...series.dates],
    dailyYi: [...series.daily_inflow_yi],
    cumYi: [...series.cum_inflow_yi],
    indexClose: [...series.index_close],
    etfCount: [...series.etf_count],
  };
}

/** 亿元文本：带正负号，两位小数。 */
export function formatSignedYi(value: number): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}`;
}

/** 万亿/亿自适应文本：累计值量级大，超过 1 万亿转“万亿”，否则用“亿”。 */
export function formatCumYi(value: number): string {
  if (Math.abs(value) >= 10000) {
    return `${formatSignedYi(value / 10000)} 万亿`;
  }
  return `${formatSignedYi(value)} 亿`;
}

export interface BroadInflow6yPalette {
  /** 累计净流入主线 */
  cum: string;
  /** 日频净流入副图 */
  daily: string;
  index: string;
  text: string;
  grid: string;
  zeroLine: string;
}

/** 日期轴标签：6 年跨度下用「年-月」，避免完整日期挤在一起。 */
function axisLabel(value: string): string {
  return value.length >= 7 ? value.slice(0, 7) : value;
}

/**
 * 双面板折线 option：上层累计净流入（左轴）+ 上证指数（右轴），下层日频净流入。
 * 两块 grid 共用同一份日期与同一个 dataZoom，拖动滑块时同步收窄。
 */
export function buildBroadInflow6yOption(
  view: BroadInflow6yView,
  palette: BroadInflow6yPalette,
) {
  const lastIndex = view.dates.length - 1;
  return {
    animation: false,
    legend: {
      top: 0,
      left: 0,
      textStyle: { color: palette.text, fontSize: 11 },
      data: ['累计净流入', '上证指数', '日频净流入'],
    },
    grid: [
      { left: 12, right: 16, top: 30, height: '42%', containLabel: true },
      { left: 12, right: 16, top: '58%', height: '20%', containLabel: true },
    ],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: palette.text, width: 1 } },
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: palette.grid,
      textStyle: { color: '#334155', fontSize: 12 },
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params];
        const first = list[0] as { dataIndex?: number } | undefined;
        const index = first?.dataIndex ?? -1;
        const day = index >= 0 ? view.dates[index] ?? '' : '';
        const lines = [day];
        for (const raw of list as { seriesName?: string; marker?: string; value?: number | null }[]) {
          const value = raw.value;
          if (value === null || value === undefined || Number.isNaN(value)) continue;
          const text = raw.seriesName === '上证指数'
            ? value.toFixed(2)
            : `${formatSignedYi(value)} 亿`;
          lines.push(`${raw.marker ?? ''}${raw.seriesName ?? ''}：${text}`);
        }
        if (index >= 0 && view.etfCount[index] !== undefined) {
          lines.push(`当日宽基池：${view.etfCount[index]} 只`);
        }
        return lines.join('<br/>');
      },
    },
    xAxis: [
      {
        type: 'category',
        gridIndex: 0,
        data: view.dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: palette.grid } },
        axisTick: { show: false },
        axisLabel: { show: false },
        axisPointer: { label: { show: false } },
      },
      {
        type: 'category',
        gridIndex: 1,
        data: view.dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: palette.grid } },
        axisTick: { show: false },
        axisLabel: {
          color: palette.text, fontSize: 10, hideOverlap: true,
          formatter: (value: string) => axisLabel(value),
        },
      },
    ],
    yAxis: [
      {
        type: 'value',
        gridIndex: 0,
        name: '累计（亿元）',
        nameTextStyle: { color: palette.text, fontSize: 10 },
        scale: true,
        axisLabel: { color: palette.text, fontSize: 10 },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: palette.grid } },
      },
      {
        type: 'value',
        gridIndex: 0,
        name: '上证指数',
        nameTextStyle: { color: palette.text, fontSize: 10 },
        position: 'right',
        scale: true,
        axisLabel: { color: palette.text, fontSize: 10 },
        axisLine: { show: false },
        splitLine: { show: false },
      },
      {
        type: 'value',
        gridIndex: 1,
        name: '日频（亿元）',
        nameTextStyle: { color: palette.text, fontSize: 10 },
        scale: true,
        axisLabel: { color: palette.text, fontSize: 10 },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: palette.grid } },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      {
        type: 'slider', xAxisIndex: [0, 1], start: 0, end: 100,
        bottom: 6, height: 18,
        borderColor: palette.grid, fillerColor: 'rgba(148,163,184,0.18)',
        handleStyle: { color: '#cbd5e1' },
        textStyle: { color: palette.text, fontSize: 10 },
        dataBackground: {
          lineStyle: { color: palette.cum, opacity: 0.4 },
          areaStyle: { color: palette.cum, opacity: 0.12 },
        },
      },
    ],
    series: [
      {
        name: '累计净流入',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: view.cumYi,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.6, color: palette.cum },
        itemStyle: { color: palette.cum },
        areaStyle: { color: palette.cum, opacity: 0.08 },
        z: 3,
      },
      {
        name: '上证指数',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 1,
        data: view.indexClose,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1, color: palette.index, opacity: 0.9 },
        itemStyle: { color: palette.index },
        z: 2,
      },
      {
        name: '日频净流入',
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 2,
        data: view.dailyYi,
        showSymbol: false,
        lineStyle: { width: 1, color: palette.daily },
        itemStyle: { color: palette.daily },
        // 零轴虚线：日频正负方向靠位置表达，不靠颜色，避免把流出段误读成流入
        markLine: {
          silent: true,
          symbol: 'none',
          label: { show: false },
          lineStyle: { color: palette.zeroLine, type: 'dashed', width: 1 },
          data: [{ yAxis: 0 }],
        },
        z: 3,
      },
    ],
    // 6 年全区间默认全部可见；首尾各留 1 个点，避免首末点被 grid 边缘裁掉
    _bounds: { first: 0, last: lastIndex },
  };
}

/** 统计口径：近 N 个交易日净流入之和，不足 N 日按实际天数求和。 */
export function sumRecent(values: number[], days: number): number {
  const tail = values.slice(-days);
  return Math.round(tail.reduce((total, value) => total + value, 0) * 100) / 100;
}