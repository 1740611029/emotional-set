import test from 'node:test';
import assert from 'node:assert/strict';
import type { Candidate, Signal } from '../src/data/types.ts';
import {
  buildIndexSignalChartOption,
  buildIndexSignalMarkers,
  DEFAULT_INDEX_CHART_RANGE_KEY,
  formatCandidateLayers,
  formatIndexSignalMarkerTooltip,
  INDEX_CHART_RANGES,
  indexChartRangeOption,
  sliceIndexHistory,
} from '../src/lib/indexSignalCharts.ts';

const history = Array.from({ length: 10 }, (_, index) => ({
  date: `2026-01-${String(index + 1).padStart(2, '0')}`,
  close: 100 + index,
}));

test('INDEX_CHART_RANGES 保持用户要求的标签顺序和交易日映射', () => {
  assert.deepEqual(
    INDEX_CHART_RANGES.map(({ key, label, tradingDays }) => ({ key, label, tradingDays })),
    [
      { key: '1m', label: '近一个月', tradingDays: 22 },
      { key: '3m', label: '近三个月', tradingDays: 66 },
      { key: '6m', label: '近半年', tradingDays: 126 },
      { key: '1y', label: '近一年', tradingDays: 252 },
      { key: '2y', label: '近两年', tradingDays: 504 },
      { key: '3y', label: '近三年', tradingDays: 756 },
      { key: 'all', label: '全部', tradingDays: null },
    ],
  );
  assert.equal(indexChartRangeOption('1y').tradingDays, 252);
  assert.equal(DEFAULT_INDEX_CHART_RANGE_KEY, '1y');
});

test('sliceIndexHistory 支持全部、短历史、空历史和统一结束日', () => {
  assert.equal(sliceIndexHistory(history, 'all').length, 10);
  assert.deepEqual(sliceIndexHistory(history.slice(0, 3), '1y'), history.slice(0, 3));
  assert.deepEqual(sliceIndexHistory([], '1y'), []);

  const withFutureDate = [...history, { date: '2026-01-11', close: 110 }];
  const sliced = sliceIndexHistory(withFutureDate, 'all', '2026-01-05');
  assert.equal(sliced.at(-1)?.date, '2026-01-05');
  assert.equal(sliced.some((row) => row.date === '2026-01-11'), false);
});

test('buildIndexSignalMarkers 映射底部、顶部、候选并过滤范围外信号', () => {
  const signals: Signal[] = [
    { date: '2026-01-02', type: 'bottom', price: 98.5 },
    { date: '2026-01-04', type: 'top', price: 104.2 },
    { date: '2025-12-01', type: 'bottom', price: 90 },
  ];
  const candidates: Candidate[] = [
    {
      date: '2026-01-03', type: 'bottom', price: 99.1,
      layers: { price: true, momentum: false, volume: true },
      passed: 2, trading_days_since: 1,
    },
    {
      date: '2026-01-05', type: 'top', price: 105,
      layers: { price: true, momentum: true, volume: false },
      passed: 2, trading_days_since: 2,
    },
  ];

  const markers = buildIndexSignalMarkers(signals, candidates, history.slice(1, 4).map((row) => row.date));
  assert.deepEqual(
    markers.map(({ status, direction, date }) => ({ status, direction, date })),
    [
      { status: 'confirmed', direction: 'bottom', date: '2026-01-02' },
      { status: 'candidate', direction: 'bottom', date: '2026-01-03' },
      { status: 'confirmed', direction: 'top', date: '2026-01-04' },
    ],
  );
  assert.equal(markers[0].price, 98.5);
  assert.equal(markers[1].layers?.momentum, false);
  assert.equal(markers[1].tradingDaysSince, 1);
  assert.deepEqual(buildIndexSignalMarkers([], [], history.map((row) => row.date)), []);
});

test('候选确认进度按价格、动量、量能输出通过状态，且不会输出为已确认', () => {
  const layers = { price: true, momentum: false, volume: true };
  assert.equal(formatCandidateLayers(layers), '价格 通过 · 动量 未通过 · 量能 通过');

  const tooltip = formatIndexSignalMarkerTooltip({
    status: 'candidate',
    direction: 'bottom',
    date: '2026-01-03',
    price: 99.1,
    layers,
    tradingDaysSince: 1,
  });
  assert.match(tooltip, /状态 待确认/);
  assert.match(tooltip, /方向 底部/);
  assert.match(tooltip, /触发价 99\.10/);
  assert.match(tooltip, /价格 通过 · 动量 未通过 · 量能 通过/);
  assert.match(tooltip, /已等待 1 个交易日/);
  assert.doesNotMatch(tooltip, /状态 已确认/);
});

test('buildIndexSignalChartOption 生成收盘价折线与状态可区分的 marker 配置', () => {
  const markers = buildIndexSignalMarkers(
    [
      { date: '2026-01-01', type: 'bottom', price: 100 },
      { date: '2026-01-02', type: 'top', price: 103 },
    ],
    [
      {
        date: '2026-01-03', type: 'bottom', price: 101,
        layers: { price: true, momentum: true, volume: false },
        passed: 2, trading_days_since: 1,
      },
    ],
    history.slice(0, 3).map((row) => row.date),
  );
  const option = buildIndexSignalChartOption(
    history.slice(0, 3).map((row) => row.date),
    [100, 101, 102],
    markers,
  );

  assert.equal(option.series[0].type, 'line');
  assert.deepEqual(option.series[0].data, [100, 101, 102]);
  const markerData = option.series[0].markPoint.data;
  assert.equal(markerData.length, 3);

  assert.equal(markerData[0].symbolRotate, 0);
  assert.equal(markerData[0].itemStyle.color, '#14b8a6');
  assert.equal(markerData[1].symbolRotate, 180);
  assert.equal(markerData[1].itemStyle.color, '#f26522');
  assert.equal(markerData[2].itemStyle.color, '#ffffff');
  assert.equal(markerData[2].itemStyle.borderColor, '#14b8a6');
  assert.equal(markerData[2].itemStyle.borderWidth, 2);
  assert.deepEqual(markerData[1].coord, ['2026-01-02', 103]);

  assert.equal(option.tooltip.trigger, 'axis');
  assert.match(option.tooltip.formatter({ seriesType: 'line', dataIndex: 1 }), /2026-01-02/);
  assert.match(option.tooltip.formatter({ seriesType: 'line', dataIndex: 1 }), /收盘 101\.00/);
  assert.match(option.tooltip.formatter({ seriesType: 'markPoint', data: markerData[0] }), /状态 已确认/);
  assert.match(option.tooltip.formatter({ seriesType: 'markPoint', data: markerData[2] }), /状态 待确认/);
});

test('折线悬停 tooltip 展示当前点位，当日有信号时追加信号信息', () => {
  const dates = history.slice(0, 4).map((row) => row.date);
  const closes = [100, 101, 102, 103];
  const markers = buildIndexSignalMarkers(
    [{ date: '2026-01-02', type: 'top', price: 103 }],
    [
      {
        date: '2026-01-03', type: 'bottom', price: 99.1,
        layers: { price: true, momentum: false, volume: true },
        passed: 2, trading_days_since: 1,
      },
    ],
    dates,
  );
  const option = buildIndexSignalChartOption(dates, closes, markers);

  const plain = option.tooltip.formatter([{ seriesType: 'line', dataIndex: 3, axisValue: '2026-01-04' }]);
  assert.match(plain, /2026-01-04/);
  assert.match(plain, /收盘 103\.00/);
  assert.doesNotMatch(plain, /状态/);

  const withConfirmed = option.tooltip.formatter([{ seriesType: 'line', dataIndex: 1, axisValue: '2026-01-02' }]);
  assert.match(withConfirmed, /收盘 101\.00/);
  assert.match(withConfirmed, /状态 已确认/);
  assert.match(withConfirmed, /方向 顶部/);
  assert.match(withConfirmed, /触发价 103\.00/);

  const withCandidate = option.tooltip.formatter([{ seriesType: 'line', dataIndex: 2, axisValue: '2026-01-03' }]);
  assert.match(withCandidate, /状态 待确认/);
  assert.match(withCandidate, /价格 通过 · 动量 未通过 · 量能 通过/);
  assert.doesNotMatch(withCandidate, /状态 已确认/);
});

test('盘中模式下的已确认信号标记显示为盘中临时且未确认', () => {
  const marker = formatIndexSignalMarkerTooltip({
    status: 'confirmed',
    direction: 'top',
    date: '2026-01-02',
    price: 103,
  }, true);
  assert.match(marker, /状态 盘中临时（未确认）/);
  assert.doesNotMatch(marker, /状态 已确认/);
});

test('信号图表保持既有信号配置和 tooltip 文案', () => {
  const dates = history.slice(0, 3).map((row) => row.date);
  const closes = [100, 101, 102];
  const markers = buildIndexSignalMarkers(
    [{ date: '2026-01-02', type: 'top', price: 101 }],
    [],
    dates,
  );
  const option = buildIndexSignalChartOption(dates, closes, markers);

  assert.equal(option.series[0].markPoint.data.length, 1);
  const tooltip = option.tooltip.formatter([
    { seriesType: 'line', dataIndex: 1, axisValue: '2026-01-02' },
  ]);
  assert.doesNotMatch(tooltip, /ETF 放量/);
  assert.equal(tooltip,
    '2026-01-02<br/>收盘 101.00<br/>2026-01-02<br/>状态 已确认<br/>方向 顶部<br/>触发价 101.00');
  assert.equal(option.tooltip.formatter({
    seriesType: 'markPoint', data: option.series[0].markPoint.data[0],
  }), '2026-01-02<br/>状态 已确认<br/>方向 顶部<br/>触发价 101.00');
  assert.equal(option.tooltip.formatter({ seriesType: 'line', dataIndex: 1 }),
    '2026-01-02<br/>收盘 101.00<br/>2026-01-02<br/>状态 已确认<br/>方向 顶部<br/>触发价 101.00');
});

test('基础 option 不再为放量标记让位设置 yAxis.min', () => {
  const empty = buildIndexSignalChartOption(['2026-11-03'], [100], []);
  assert.equal('min' in empty.yAxis, false);
});
