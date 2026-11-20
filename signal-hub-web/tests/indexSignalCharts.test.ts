import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_INDEX_CHART_RANGE_KEY,
  INDEX_CHART_RANGES,
  indexChartRangeOption,
} from '../src/lib/indexSignalCharts.ts';

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
