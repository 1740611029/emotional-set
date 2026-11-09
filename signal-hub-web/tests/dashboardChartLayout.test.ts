import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DASHBOARD_INDEX_CHART_ORDER,
  orderIndicesForCharts,
} from '../src/lib/dashboardChartLayout.ts';

interface CodeItem {
  code: string;
  name: string;
}

const byCode = (codes: string[]): CodeItem[] =>
  codes.map((code) => ({ code, name: code }));

test('orderIndicesForCharts 按目标四行分组顺序重排 7 大指数', () => {
  // 传入数据端口径的原始顺序，验证输出改为用户指定的展示顺序
  const source = byCode([
    'sh000001', 'sz399001', 'sz399006', 'sh000688', 'sh000300', 'sh000905', 'sh000852',
  ]);
  const ordered = orderIndicesForCharts(source);
  assert.deepEqual(ordered.map((item) => item.code), [...DASHBOARD_INDEX_CHART_ORDER]);
  assert.deepEqual(
    ordered.map((item) => item.code),
    ['sh000001', 'sh000688', 'sz399006', 'sz399001', 'sh000300', 'sh000905', 'sh000852'],
  );
});

test('快照缺项时只返回现有指数并保持相对顺序', () => {
  const source = byCode(['sh000300', 'sh000001', 'sz399006']);
  const ordered = orderIndicesForCharts(source);
  assert.deepEqual(ordered.map((item) => item.code), ['sh000001', 'sz399006', 'sh000300']);
});

test('未知 code 保留原始相对顺序追加到已知项末尾', () => {
  const source = byCode(['sh999999', 'sh000001', 'sz888888', 'sh000688']);
  const ordered = orderIndicesForCharts(source);
  assert.deepEqual(
    ordered.map((item) => item.code),
    ['sh000001', 'sh000688', 'sh999999', 'sz888888'],
  );
});

test('orderIndicesForCharts 返回新数组且不就地修改入参', () => {
  const source = byCode(['sh000300', 'sh000001']);
  const before = source.map((item) => item.code);
  const ordered = orderIndicesForCharts(source);
  assert.notEqual(ordered, source);
  assert.deepEqual(source.map((item) => item.code), before);
});
