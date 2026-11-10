import test from 'node:test';
import assert from 'node:assert/strict';
import type { InstitutionMeta, InstitutionRelation } from '../src/data/types.ts';
import { INSTITUTION_DISPLAY_ORDER, orderInstitutions, windowRelationStats } from '../src/lib/institutionView.ts';

const meta = (id: InstitutionMeta['id'], name: string): InstitutionMeta => ({ id, name, note: `${name}口径` });

test('固定展示顺序：中信期货排到国泰君安之前，其他机构在最后', () => {
  const input = [meta('guotai', '国泰君安'), meta('citic', '中信期货'), meta('others', '其他机构')];
  const ordered = orderInstitutions(input);
  assert.deepEqual(ordered.map((item) => item.id), ['citic', 'guotai', 'others']);
  // 不修改入参、不改变元数据内容
  assert.deepEqual(input.map((item) => item.id), ['guotai', 'citic', 'others']);
  assert.deepEqual(INSTITUTION_DISPLAY_ORDER, ['citic', 'guotai', 'others']);
});

test('固定展示顺序：缺失机构保持顺序且不报错', () => {
  const ordered = orderInstitutions([meta('guotai', '国泰君安')]);
  assert.deepEqual(ordered.map((item) => item.id), ['guotai']);
  const withUnknown = orderInstitutions([meta('guotai', '国泰君安'), meta('citic', '中信期货')]);
  assert.deepEqual(withUnknown.map((item) => item.id), ['citic', 'guotai']);
});

test('滑动窗口同向率：待判定不计入分母，起止区间取已判定交易日', () => {
  const dates = Array.from({ length: 10 }, (_, i) => `2026-10-${String(i + 1).padStart(2, '0')}`);
  // 7 个已判定（4 同向 + 3 对冲）+ 2 待判定 + 1 无数据
  const relation: InstitutionRelation[] = ['S', 'S', 'H', 'S', 'H', 'S', 'H', 'P', 'P', 'N'];
  const stat = windowRelationStats(relation, dates, 10);
  assert.equal(stat.decided, 7);
  assert.equal(stat.same, 4);
  assert.equal(stat.hedge, 3);
  assert.equal(stat.pending, 2);
  assert.equal(stat.no_data, 1);
  assert.ok(stat.rate !== null && Math.abs(stat.rate - 4 / 7) < 1e-12);
  assert.equal(stat.start, dates[0]);
  assert.equal(stat.end, dates[6]);
  assert.equal(stat.small_sample, true);
});

test('滑动窗口同向率：不足窗口时按可用天数计算', () => {
  const dates = ['2026-11-03', '2026-11-04', '2026-11-05'];
  const relation: InstitutionRelation[] = ['S', 'H', 'P'];
  const stat = windowRelationStats(relation, dates, 20);
  assert.equal(stat.decided, 2);
  assert.ok(stat.rate !== null && Math.abs(stat.rate - 0.5) < 1e-12);
  assert.equal(stat.start, dates[0]);
  assert.equal(stat.end, dates[1]);
});

test('滑动窗口同向率：只取尾部 N 个交易日，窗口外数据不参与', () => {
  const dates = Array.from({ length: 25 }, (_, i) => `2026-10-${String(i + 1).padStart(2, '0')}`);
  // 前 15 天全对冲，后 10 天全同向；近 10 日窗口应只看尾部 10 个同向
  const relation: InstitutionRelation[] = [
    ...Array.from({ length: 15 }, () => 'H' as const),
    ...Array.from({ length: 10 }, () => 'S' as const),
  ];
  const d10 = windowRelationStats(relation, dates, 10);
  assert.equal(d10.decided, 10);
  assert.equal(d10.same, 10);
  assert.ok(d10.rate !== null && Math.abs(d10.rate - 1) < 1e-12);
  assert.equal(d10.start, dates[15]);
  const d20 = windowRelationStats(relation, dates, 20);
  assert.equal(d20.decided, 20);
  assert.equal(d20.same, 10);
  assert.ok(d20.rate !== null && Math.abs(d20.rate - 0.5) < 1e-12);
});

test('滑动窗口同向率：无已判定样本时比率为 null', () => {
  const dates = ['2026-11-03', '2026-11-04'];
  const stat = windowRelationStats(['P', 'N'], dates, 10);
  assert.equal(stat.decided, 0);
  assert.equal(stat.rate, null);
  assert.equal(stat.start, null);
  assert.equal(stat.end, null);
});
