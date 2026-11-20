import test from 'node:test';
import assert from 'node:assert/strict';
import type { EtfShareFlow } from '../src/data/types.ts';
import { normalizeEtfShareFlow } from '../src/data/client.ts';
import {
  buildShareFlowView,
  buildShareFlowOption,
  formatSignedYi,
  sumRecent,
} from '../src/lib/etfShareFlow.ts';

function flow(overrides: Partial<EtfShareFlow> = {}): EtfShareFlow {
  const dates = ['2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23', '2026-10-26'];
  return {
    data_state: 'ready',
    as_of: dates[dates.length - 1],
    generated_at: '2026-11-20T15:00:00+08:00',
    stale: false,
    stale_reason: null,
    coverage: {
      start: dates[0], end: dates[dates.length - 1], trading_days: dates.length,
      etf_count: 124, min_counted: 110, max_counted: 124,
    },
    dates,
    net_inflow_yi: [100, -50, 30, 0, 478.79],
    index_close: [3580, 3585, null, 3590, 3596],
    stats: { last_day_yi: 478.79, last_60d_yi: 558.79, last_60d_days: 5 },
    universe: [{ code: '510300', name: '沪深300ETF', index: '沪深300' }],
    sources: { share: 'sse+szse', nav: 'ths' },
    ...overrides,
  };
}

test('契约校验：缺数组字段报错，preparing 状态仍返回完整结构', () => {
  assert.throws(() => normalizeEtfShareFlow({ data_state: 'ready', dates: [] }));
  const preparing = normalizeEtfShareFlow({
    ...flow(), data_state: 'preparing', dates: [], net_inflow_yi: [], index_close: [],
  });
  assert.equal(preparing.data_state, 'preparing');
  assert.deepEqual(preparing.net_inflow_yi, []);
});

test('全量视图原样映射，不裁切', () => {
  const data = flow();
  const view = buildShareFlowView(data);
  assert.deepEqual(view.dates, data.dates);
  assert.deepEqual(view.inflowYi, data.net_inflow_yi);
  assert.deepEqual(view.indexClose, data.index_close);
  // 不共享引用：切片不会污染原始产物
  view.inflowYi[0] = 999;
  assert.equal(data.net_inflow_yi[0], 100);
});

test('近 N 日求和：不足 N 日按实际天数', () => {
  assert.equal(sumRecent([100, -50, 30, 0, 478.79], 60), 558.79);
  assert.equal(sumRecent([100, -50, 30, 0, 478.79], 2), 478.79);
  assert.equal(sumRecent([], 60), 0);
});

test('带符号文本：正号显式、负号自带、零无正号', () => {
  assert.equal(formatSignedYi(478.79), '+478.79');
  assert.equal(formatSignedYi(-50), '-50.00');
  assert.equal(formatSignedYi(0), '0.00');
});

test('双轴 option：红正绿负逐柱着色、指数缺失留 null、含 slider 与 inside 两个 dataZoom', () => {
  const option: any = buildShareFlowOption(buildShareFlowView(flow()), {
    inflow: '#f26522', outflow: '#3a9a6a', index: '#94a3b8', text: '#64748b', grid: '#f1f5f9',
  });
  assert.equal(option.series[0].type, 'bar');
  assert.equal(option.series[1].type, 'line');
  assert.equal(option.series[0].data[0].itemStyle.color, '#f26522'); // 正流入红
  assert.equal(option.series[0].data[1].itemStyle.color, '#3a9a6a'); // 净流出绿
  assert.equal(option.series[1].data[2], null); // 指数缺失不插值
  assert.equal(option.dataZoom.length, 2);
  assert.equal(option.dataZoom[1].type, 'slider'); // 横向时间滑块
  assert.equal(option.series[1].yAxisIndex, 1);
});
