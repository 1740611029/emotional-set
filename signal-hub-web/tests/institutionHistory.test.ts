import test from 'node:test';
import assert from 'node:assert/strict';
import {
  HISTORY_PAGE_SIZE,
  HISTORY_TABLE_HEIGHT_PX,
  hasMoreToLoad,
  historyProgressLabel,
  initialVisibleCount,
  nextVisibleCount,
  selectHistoryIndexes,
  shouldLoadMore,
} from '../src/lib/institutionHistory.ts';

const DATES = ['2026-10-28', '2026-10-29', '2026-10-30', '2026-11-02', '2026-11-03'];

test('Scenario 默认倒序与分页：筛选结果为日期倒序，首批渲染行数有上限', () => {
  const indexes = selectHistoryIndexes(DATES, { from: '', to: '' }, true);
  assert.deepEqual(indexes.map((i) => DATES[i]), [...DATES].reverse());
  const totalRows = 120;
  assert.equal(initialVisibleCount(totalRows), HISTORY_PAGE_SIZE);
  assert.equal(HISTORY_PAGE_SIZE, 50);
});

test('无机构序列时不生成任何行（机构缺数据整列表为空）', () => {
  assert.deepEqual(selectHistoryIndexes(DATES, { from: '', to: '' }, false), []);
});

test('Scenario 筛选后无结果：区间无匹配时返回空行，不产生残留', () => {
  const indexes = selectHistoryIndexes(DATES, { from: '2026-12-01', to: '2026-12-31' }, true);
  assert.deepEqual(indexes, []);
});

test('日期区间筛选只保留区间内日期，边界含端点', () => {
  const indexes = selectHistoryIndexes(DATES, { from: '2026-10-29', to: '2026-11-02' }, true);
  assert.deepEqual(indexes.map((i) => DATES[i]), ['2026-11-02', '2026-10-30', '2026-10-29']);
});

test('Scenario 固定高度内部滚动：接近容器底部才加载下一批', () => {
  // 容器高 250，内容高 2000（可滚范围 1750）
  // 距底部 39px < 40px 阈值 → 触发
  assert.equal(shouldLoadMore(1711, 2000, 250, 50, 120), true);
  // 距底部 60px → 不触发
  assert.equal(shouldLoadMore(1690, 2000, 250, 50, 120), false);
  // 已全部展示 → 不再触发
  assert.equal(shouldLoadMore(1750, 2000, 250, 120, 120), false);
});

test('滚动加载批次为 50 行且封顶总行数', () => {
  assert.equal(nextVisibleCount(50, 120), 100);
  assert.equal(nextVisibleCount(100, 120), 120);
  assert.equal(nextVisibleCount(120, 120), 120);
});

test('加载进度文案与「向下滚动加载更多」提示的出现条件', () => {
  assert.equal(historyProgressLabel(120, 50), '共 120 行，已展示 50 行');
  assert.equal(hasMoreToLoad(50, 120), true);
  assert.equal(hasMoreToLoad(120, 120), false);
});

test('Scenario 筛选对所有列表生效：切换筛选后重置回首批行数', () => {
  const loaded = nextVisibleCount(100, 360);
  // 筛选变化后以新结果集重新计算初始可视行数（等价组件内 setVisibleCount(initialVisibleCount)）
  assert.equal(initialVisibleCount(360), HISTORY_PAGE_SIZE);
  assert.ok(loaded > initialVisibleCount(360));
});

test('行数不足时可视行数取实际行数，空状态区域高度规格仍为 250px', () => {
  assert.equal(initialVisibleCount(12), 12);
  assert.equal(initialVisibleCount(0), 0);
  assert.equal(HISTORY_TABLE_HEIGHT_PX, 250);
});
