import test from 'node:test';
import assert from 'node:assert/strict';
import {
  PRECISION_HORIZONS, PRECISION_KINDS, precisionMatrix, wilsonInterval,
} from '../src/lib/precisionMatrix.ts';
import {
  buildIndexSignalChartOption,
  DEFAULT_INDEX_SIGNAL_CHART_PALETTE,
} from '../src/lib/indexSignalCharts.ts';
import type {
  PrecisionCell, PrecisionHorizon, PrecisionSegment, SignalKind, Snapshot,
  PrecisionPivotAnchor,
} from '../src/data/types.ts';

const INTERVAL = { start: '2020-10-30', end: '2024-11-19' };

function makeCell(overrides: Partial<PrecisionCell> = {}): PrecisionCell {
  return {
    segment: 'train',
    horizon_days: 20,
    direction: 'bottom',
    statistical_interval: INTERVAL,
    triggers: 4,
    resolved: 3,
    n_resolved: 3,
    successes: 2,
    failures: 1,
    pending: 1,
    direction_correct_rate: 2 / 3,
    direction_correct_wilson95: [0.2, 0.9],
    effective_amplitude_successes: 1,
    effective_amplitude_rate: 1 / 3,
    effective_amplitude_wilson95: [0.1, 0.7],
    effective_amplitude_line: 0.0425,
    small_sample: true,
    baseline: {
      baseline_samples: 4, baseline_successes: 2, baseline_rate: 0.5,
      signal_rate: 2 / 3, difference: 1 / 6, seed: 20261109, horizon_days: 20,
    },
    drawdown: {
      avg_max_adverse_excursion: -0.02,
      worst_max_adverse_excursion: -0.05,
      avg_forward_return_20: 0.03,
      avg_forward_return_30: 0.04,
      return_drawdown_ratio: 1.5,
      mae_exceeded_failures: 1,
      mae_exceeded_failure_share: 0.25,
    },
    ...overrides,
  };
}

function makeSegment(segment: PrecisionSegment, scale = 1) {
  const horizons = {} as Record<PrecisionHorizon, Record<SignalKind, PrecisionCell>>;
  for (const horizon of PRECISION_HORIZONS) {
    horizons[horizon] = {
      bottom: makeCell({ segment, horizon_days: Number(horizon), direction: 'bottom',
        triggers: 4 * scale, successes: 2 * scale, resolved: 3 * scale }),
      top: makeCell({ segment, horizon_days: Number(horizon), direction: 'top',
        triggers: 4 * scale, successes: 2 * scale, resolved: 3 * scale }),
    };
  }
  return { statistical_interval: INTERVAL, triggers: 8 * scale, horizons };
}

function makeSnapshot(indexCount = 2): Snapshot {
  const indices = Array.from({ length: indexCount }, (_, position) => ({
    code: `idx${position}`,
    name: `指数${position}`,
    precision_matrix: {
      train: makeSegment('train'),
      validation: makeSegment('validation'),
    },
  }));
  return { indices } as unknown as Snapshot;
}

test('Wilson 95% 区间与后端同口径：6/6 全对下界约 60.97%', () => {
  const [low, high] = wilsonInterval(6, 6);
  assert.ok(low !== null && Math.abs(low - 0.6096657120978346) < 1e-6);
  assert.equal(high, 1);
  assert.deepEqual(wilsonInterval(0, 0), [null, null]);
  const [zeroLow, zeroHigh] = wilsonInterval(0, 8);
  assert.equal(zeroLow, 0);
  assert.ok(zeroHigh !== null && zeroHigh > 0 && zeroHigh < 1);
});

test('空快照每格均不可用，前端应显示 -- 而不是 0%', () => {
  const view = precisionMatrix(null, 'train');
  for (const horizon of PRECISION_HORIZONS) {
    for (const kind of PRECISION_KINDS) {
      const cell = view[horizon][kind];
      assert.equal(cell.available, false);
      assert.equal(cell.directionRate, null);
      assert.equal(cell.amplitudeRate, null);
      assert.equal(cell.triggers, 0);
      assert.deepEqual(cell.wilson, [null, null]);
    }
  }
});

test('旧版产物缺 precision_matrix 时整张矩阵保持空态', () => {
  const legacy = { indices: [{ code: 'sh000001', name: '上证指数' }] } as unknown as Snapshot;
  const view = precisionMatrix(legacy, 'validation');
  assert.equal(view['20']['bottom'].available, false);
});

test('7 指数合并：触发次数守恒、双口径分母一致、Wilson 重算', () => {
  const view = precisionMatrix(makeSnapshot(2), 'train');
  const cell = view['20']['bottom'];
  assert.equal(cell.available, true);
  assert.equal(cell.triggers, 8);
  assert.equal(cell.resolved, 6);
  assert.equal(cell.successes, 4);
  assert.equal(cell.failures, 2);
  assert.equal(cell.pending, 2);
  assert.equal(cell.directionRate, 4 / 6);
  assert.equal(cell.amplitudeRate, 2 / 6);
  assert.deepEqual(cell.wilson, wilsonInterval(4, 6));
  assert.equal(cell.avgMae, -0.02);
  assert.equal(cell.maeFailures, 2);
  assert.deepEqual(cell.interval, INTERVAL);
  assert.equal(cell.amplitudeLine, 0.0425);
});

test('已判定样本为个位数时如实标记，不隐藏', () => {
  const view = precisionMatrix(makeSnapshot(1), 'validation');
  assert.equal(view['5']['top'].singleDigit, true);
  assert.equal(view['5']['top'].smallSample, true);
  assert.equal(view['5']['top'].resolved, 3);
});

test('研究段与独立验证段各自独立取数', () => {
  const snapshot = makeSnapshot(1);
  const matrix = snapshot.indices[0].precision_matrix as unknown as {
    validation: { horizons: Record<string, Record<string, PrecisionCell>> };
  };
  matrix.validation.horizons['5'].bottom.triggers = 99;
  assert.equal(precisionMatrix(snapshot, 'train')['5'].bottom.triggers, 4);
  assert.equal(precisionMatrix(snapshot, 'validation')['5'].bottom.triggers, 99);
});

const ANCHOR: PrecisionPivotAnchor = {
  date: '2024-01-04',
  type: 'bottom',
  price: 101,
  pivot_date: '2024-01-02',
  pivot_price: 98,
  natural_confirm_date: '2024-01-03',
  natural_confirm_offset: 1,
  signal_confirm_offset_from_pivot: 2,
  signal_confirm_offset_from_natural: 1,
  confirmation: [],
};

test('折线图三类日期使用三种不同标记样式', () => {
  const dates = ['2024-01-02', '2024-01-03', '2024-01-04'];
  const markers = [{
    status: 'confirmed' as const, direction: 'bottom' as const,
    date: '2024-01-04', price: 101,
  }];
  const option = buildIndexSignalChartOption(
    dates, [98, 99, 101], markers, false, DEFAULT_INDEX_SIGNAL_CHART_PALETTE, [ANCHOR],
  );
  const symbols = option.series[0].markPoint.data.map((item) => item.symbol);
  assert.ok(symbols.includes('diamond'), '缺少拐点基准日菱形');
  assert.ok(symbols.includes('rect'), '缺少自然确认日方形');
  assert.ok(symbols.includes('triangle'), '缺少信号确认日三角');
  assert.equal(new Set(symbols).size, 3);
});

test('hover 拐点基准日明确标注事后标注且不表述为信号发出日', () => {
  const dates = ['2024-01-02', '2024-01-03', '2024-01-04'];
  const option = buildIndexSignalChartOption(
    dates, [98, 99, 101], [], false, DEFAULT_INDEX_SIGNAL_CHART_PALETTE, [ANCHOR],
  );
  const text = option.tooltip.formatter([{ seriesType: 'line', dataIndex: 0 }]);
  assert.match(text, /事后标注/);
  assert.match(text, /不代表信号发出的时点/);
  assert.doesNotMatch(text, /信号发出日/);
});

test('自然确认日与信号确认日各自的提示也可查', () => {
  const dates = ['2024-01-02', '2024-01-03', '2024-01-04'];
  const option = buildIndexSignalChartOption(
    dates, [98, 99, 101], [], false, DEFAULT_INDEX_SIGNAL_CHART_PALETTE, [ANCHOR],
  );
  const natural = option.tooltip.formatter([{ seriesType: 'line', dataIndex: 1 }]);
  const signal = option.tooltip.formatter([{ seriesType: 'line', dataIndex: 2 }]);
  assert.match(natural, /拐点自然确认日/);
  assert.match(signal, /信号确认日/);
  assert.match(signal, /相对拐点 2 个交易日/);
});
