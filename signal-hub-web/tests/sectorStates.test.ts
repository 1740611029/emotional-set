import test from 'node:test';
import assert from 'node:assert/strict';
import {
  compressStates,
  explainStrength,
  formatDays,
  formatStrength,
  movingAverage,
  sortSectorItems,
} from '../src/lib/sectorStates.ts';

test('compressStates 合并连续相同状态', () => {
  const dates = ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'];
  const segments = compressStates(dates, ['bull', 'bull', 'bear', 'bear']);
  assert.deepEqual(segments, [
    { from: '2026-10-01', to: '2026-10-02', state: 'bull' },
    { from: '2026-10-03', to: '2026-10-04', state: 'bear' },
  ]);
});

test('compressStates 逐日切换与空值', () => {
  const dates = ['d1', 'd2', 'd3', 'd4'];
  const segments = compressStates(dates, ['bull', 'bear', null, 'bull']);
  assert.deepEqual(segments, [
    { from: 'd1', to: 'd1', state: 'bull' },
    { from: 'd2', to: 'd2', state: 'bear' },
    { from: 'd4', to: 'd4', state: 'bull' },
  ]);
});

test('compressStates 全空返回空数组', () => {
  assert.deepEqual(compressStates(['d1', 'd2'], [null, null]), []);
});

test('movingAverage 忽略不足窗口与缺失值', () => {
  assert.deepEqual(movingAverage([1, 2, 3, 4], 3), [null, null, 2, 3]);
  assert.deepEqual(movingAverage([1, null, 3, 4], 3), [null, null, null, null]);
});

test('explainStrength 使用统一的有向强度公式', () => {
  const parts = { range_position: 100, ma_score: 78.1 };
  const directional = explainStrength(parts, 'directional');
  assert.equal(directional.expected, 39.05);
  assert.match(directional.formula, /÷ 2 − 50 = 39\.0/);
  assert.match(directional.basisLabel, /统一公式/);

  const negative = explainStrength({ range_position: 1.9, ma_score: 0.5 }, 'directional');
  assert.equal(negative.expected, -48.8);
  assert.match(negative.formula, /-48\.8/);

  const neutral = explainStrength(parts, 'neutral');
  assert.equal(neutral.expected, 0);
  assert.match(neutral.formula, /固定为 0/);
});

test('sortSectorItems 按有符号强度降序且异常项置尾', () => {
  const rows = [
    { code: 'BK0479', name: '空头绝对值大', strength: -40, data_anomaly: false },
    { code: 'BK0433', name: '异常但为 0', strength: 0, data_anomaly: true },
    { code: 'BK0447', name: '弱正值', strength: 1, data_anomaly: false },
    { code: 'BK1283', name: '最强正值', strength: 30, data_anomaly: false },
    { code: 'BK0432', name: '缺失', strength: null, data_anomaly: false },
  ];
  assert.deepEqual(sortSectorItems(rows, 'strength').map((row) => row.name), [
    '最强正值',
    '弱正值',
    '空头绝对值大',
    '缺失',
    '异常但为 0',
  ]);
  assert.deepEqual(sortSectorItems(rows, 'name').map((row) => row.name), [
    '缺失',
    '空头绝对值大',
    '弱正值',
    '异常但为 0',
    '最强正值',
  ].sort((left, right) => left.localeCompare(right, 'zh-CN')));
  // 单层结构：排序不会合并或丢弃任何板块，也不引入层级
  assert.equal(sortSectorItems(rows, 'strength').length, rows.length);
});

test('数值格式化对空值给出占位符', () => {
  assert.equal(formatStrength(89.06), '89.1');
  assert.equal(formatStrength(89.04), '89.0');
  assert.equal(formatStrength(-30.04), '-30.0');
  assert.equal(formatStrength(0), '0.0');
  assert.equal(formatStrength(null), '—');
  assert.equal(formatDays(3), '3 日');
  assert.equal(formatDays(null), '—');
});
