import test from 'node:test';
import assert from 'node:assert/strict';
import { nextSectorParams } from '../src/lib/sectorNavigation.ts';

test('nextSectorParams 选中板块只改 code，保留筛选与检索', () => {
  const current = new URLSearchParams('state=bull&q=银行');
  const next = nextSectorParams(current, 'code', 'BK0433');
  assert.equal(next.get('code'), 'BK0433');
  assert.equal(next.get('state'), 'bull');
  assert.equal(next.get('q'), '银行');
});

test('nextSectorParams 取消选中时移除 code', () => {
  const current = new URLSearchParams('code=BK0433&sort=strength');
  const next = nextSectorParams(current, 'code', '');
  assert.equal(next.get('code'), null);
  assert.equal(next.get('sort'), 'strength');
});

test('nextSectorParams 切换筛选、排序或检索会清空选中板块', () => {
  for (const key of ['state', 'sort', 'q'] as const) {
    const current = new URLSearchParams('code=BK0433');
    const next = nextSectorParams(current, key, key === 'q' ? '银行' : 'value');
    assert.equal(next.get('code'), null, key);
    assert.equal(next.get(key), key === 'q' ? '银行' : 'value', key);
  }
});

test('nextSectorParams 返回新对象，不改动传入参数', () => {
  const current = new URLSearchParams('code=BK0433&state=all');
  const next = nextSectorParams(current, 'code', 'BK0479');
  assert.equal(current.get('code'), 'BK0433');
  assert.equal(next.get('code'), 'BK0479');
});
