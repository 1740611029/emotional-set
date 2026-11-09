import test from 'node:test';
import assert from 'node:assert/strict';
import type { EtfVolumeSeries } from '../src/data/types.ts';
import { normalizeEtfVolumeSeries } from '../src/data/client.ts';
import {
  buildEtfLinesOption,
  buildL3MarkPoints,
  buildTotalIndexOption,
  sliceEtfSeriesToView,
  toYi,
} from '../src/lib/etfVolumeSeries.ts';

function series(overrides: Partial<EtfVolumeSeries> = {}): EtfVolumeSeries {
  const dates = ['2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23', '2026-10-26'];
  return {
    data_state: 'ready',
    as_of: dates[dates.length - 1],
    generated_at: '2026-11-06T15:00:00+08:00',
    stale: false,
    stale_reason: null,
    coverage: { start: dates[0], end: dates[dates.length - 1], trading_days: dates.length },
    dates,
    etfs: [
      { code: '588000', name: '科创50ETF华夏', index: '科创50', amount: dates.map((_, i) => (i + 1) * 1_000_000_000) },
      { code: '159915', name: '创业板ETF易方达', index: '创业板指', amount: dates.map((_, i) => (i + 1) * 2_000_000_000) },
    ],
    total_amount: dates.map((_, i) => (i + 1) * 3_000_000_000),
    index_close: [3000, 3010, null, 3030, 3040],
    l3_dates: ['2026-10-22', '2024-01-01'],
    ...overrides,
  };
}

test('档位换算：近三个月取 66 日、全部取齐备区间全长', () => {
  const data = series();
  // 只有 5 个交易日，远小于 66，两档都取全部
  assert.deepEqual(sliceEtfSeriesToView(data, '3m').dates, data.dates);
  assert.deepEqual(sliceEtfSeriesToView(data, 'all').dates, data.dates);

  const long = series({
    dates: Array.from({ length: 100 }, (_, i) => `d${String(i).padStart(3, '0')}`),
    total_amount: Array.from({ length: 100 }, (_, i) => i * 1e8),
    index_close: Array.from({ length: 100 }, (_, i) => 3000 + i),
    etfs: [{ code: '510300', name: 'x', index: 'y', amount: Array.from({ length: 100 }, (_, i) => i * 1e8) }],
    l3_dates: [],
  });
  assert.equal(sliceEtfSeriesToView(long, '3m').dates.length, 66);
  assert.equal(sliceEtfSeriesToView(long, '3m').dates[0], 'd034');
  assert.equal(sliceEtfSeriesToView(long, 'all').dates.length, 100);
});

test('切片对同一区间重复调用结果完全一致', () => {
  const data = series();
  assert.deepEqual(sliceEtfSeriesToView(data, 'all'), sliceEtfSeriesToView(data, 'all'));
});

test('金额单位换算为亿元并保留一位小数', () => {
  assert.equal(toYi(12_345_678_900), 123.5);
  assert.equal(toYi(1_000_000_000), 10);
  const view = sliceEtfSeriesToView(series(), 'all');
  assert.deepEqual(view.totalYi, [30, 60, 90, 120, 150]);
});

test('L3 只保留标记本身为 L3 且落在切片内的日期', () => {
  const view = sliceEtfSeriesToView(series(), 'all');
  // 2024-01-01 不在齐备区间内，被剔除
  assert.deepEqual(view.l3Dates, ['2026-10-22']);
  const marks = buildL3MarkPoints(view, '#3a9a6a');
  assert.deepEqual(marks.map((mark) => mark.coord[0]), ['2026-10-22']);
  // 标记坐标落在该日合计成交额真实取值上（第 3 日 = 90 亿元）
  assert.equal(marks[0].coord[1], 90);
});

test('双轴图：connectNulls 为 false 且指数缺失点保持 null', () => {
  const view = sliceEtfSeriesToView(series(), 'all');
  const option = buildTotalIndexOption(view, {
    total: '#3a9a6a', index: '#f26522', l3: '#3a9a6a', text: '#64748b', grid: '#f1f5f9',
  });
  assert.equal(option.series[0].connectNulls, false);
  assert.equal(option.series[1].connectNulls, false);
  assert.equal(option.series[0].yAxisIndex, 0);
  assert.equal(option.series[1].yAxisIndex, 1);
  assert.deepEqual(option.series[1].data, [3000, 3010, null, 3030, 3040]);
});

test('六线图：共用同一亿元轴、图例含名称与代码', () => {
  const view = sliceEtfSeriesToView(series(), 'all');
  const option = buildEtfLinesOption(view, {
    text: '#64748b', grid: '#f1f5f9', lines: ['#3a9a6a', '#3a90f5'],
  });
  assert.equal(option.series.length, 2);
  assert.deepEqual(option.legend.data, ['科创50ETF华夏 588000', '创业板ETF易方达 159915']);
  for (const item of option.series) {
    assert.equal(item.connectNulls, false);
  }
});

test('normalizeEtfVolumeSeries：缺字段报错、202 准备中返回完整结构', () => {
  assert.throws(
    () => normalizeEtfVolumeSeries({ data_state: 'ready', dates: [] }),
    /缺少/,
  );
  assert.throws(() => normalizeEtfVolumeSeries({ data_state: 'weird' }), /状态非法/);

  const preparing = normalizeEtfVolumeSeries({
    data_state: 'preparing', as_of: null, generated_at: null, stale: true,
    stale_reason: 'ETF 成交额序列暂不可用', coverage: { start: null, end: null, trading_days: null },
    dates: [], etfs: [], total_amount: [], index_close: [], l3_dates: [],
  });
  assert.equal(preparing.data_state, 'preparing');
  assert.deepEqual(preparing.dates, []);
});
