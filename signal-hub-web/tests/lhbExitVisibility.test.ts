import test from 'node:test';
import assert from 'node:assert/strict';
import type { LhbOverviewTrader, LhbPosition, LhbRecord } from '../src/data/types.ts';
import {
  buildViewTraders,
  exitAgeInTradingDays,
  EXIT_VISIBILITY_DAYS,
} from '../src/lib/lhbExitVisibility.ts';

// ── 测试用交易日序列（跳过周末的连续 8 个交易日）──
const TD = [
  '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06',
  '2026-11-09', '2026-11-10', '2026-11-12', '2026-11-13',
];
const AS_OF = TD[TD.length - 1]; // 2026-11-13 (index 7)

/** 构造一条 LhbRecord */
function rec(date: string, code: string, name = code, net = 1e8): LhbRecord {
  return {
    date, code, name, reasons: ['日涨幅偏离值达7%的证券'], is_multi_day: false,
    buy: net > 0 ? net : 0, sell: net < 0 ? -net : 0, net,
    pct_chg: 5.2, board: '测试板块', confidence: 'high',
    seat_hits: ['测试席位'],
  };
}

/** 构造一个 LhbPosition（默认 last_seen === exit_date） */
function pos(code: string, status: LhbPosition['status'], exitDate: string | null): LhbPosition {
  return {
    code, name: code, status,
    exit_type: status === 'active' ? null : status === 'exited_confirmed' ? 'sell_cover' : 'silent',
    last_seen: exitDate ?? TD[0],
    exit_date: exitDate,
    first_seen: TD[0],
    buy_total: 2e8, sell_total: 1e8, net_total: 1e8,
  };
}

/** 构造一个 LhbOverviewTrader */
function trader(
  id: string, name: string,
  records: LhbRecord[],
  positions?: LhbPosition[],
): LhbOverviewTrader {
  return {
    id, name, full_name: name, tier: '极活跃', confidence: 'high',
    style: '测试风格', note: null, seats: [{ full: '测试席位', confidence: 'high' }],
    records, positions,
  };
}

// ================================================================
// 1. 核心基线：age=5 展示带徽章 / age=6 默认隐藏 / 开关回显
// ================================================================
test('5 日阈值：age=5 展示带标记，age=6 默认隐藏但 includeExpired 可回显', () => {
  const exitDate5 = TD[7 - 5]; // 2026-11-06, age=5
  const exitDate6 = TD[7 - 6]; // 2026-11-05, age=6
  const raw = trader('t1', '游资A', [
    rec(TD[0], '000001', '股票甲'),
    rec(TD[0], '000002', '股票乙'),
  ], [
    pos('000001', 'exited_confirmed', exitDate5),
    pos('000002', 'exited_confirmed', exitDate6),
  ]);

  // 默认态：age=5 保留且带标记；age=6 隐藏
  const { traders, hiddenCodes } = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  const t = traders[0];
  assert.ok(t.exits.has('000001'), 'age=5 应有离场标记');
  assert.ok(!t.records.some((r) => r.code === '000002'), 'age=6 记录应被过滤');
  assert.ok(hiddenCodes.has('000002'), 'hiddenCodes 应含 000002');
  assert.equal(hiddenCodes.size, 1);

  // 开关回显
  const { traders: shown } = buildViewTraders([raw], TD, AS_OF, { includeExpired: true });
  assert.ok(shown[0].records.some((r) => r.code === '000002'), 'includeExpired 后 age=6 记录应回显');
  assert.ok(shown[0].exits.has('000002'), 'includeExpired 后 age=6 应有标记');
});

// ================================================================
// 2. 2026-11-17 口径核心：5 日内离场始终显示，开关不影响
// ================================================================
test('5 日内离场配对默认显示带徽章，开关不影响', () => {
  const exitDate2 = TD[7 - 2]; // age=2
  const raw = trader('t1', '游资A', [
    rec(TD[0], '000001'), // 5 日内离场 → 始终显示
    rec(TD[0], '000002'), // active → 保留无徽章
  ], [
    pos('000001', 'exited_confirmed', exitDate2),
    pos('000002', 'active', null),
  ]);
  for (const includeExpired of [false, true]) {
    const { traders, hiddenCodes } = buildViewTraders([raw], TD, AS_OF, { includeExpired });
    assert.equal(traders.length, 1, `includeExpired=${includeExpired} 卡片保留`);
    assert.ok(traders[0].records.some((r) => r.code === '000001'), '5 日内离场应显示');
    assert.ok(traders[0].exits.has('000001'), '5 日内离场应带徽章');
    assert.ok(traders[0].records.some((r) => r.code === '000002'), 'active 保留');
    assert.ok(!traders[0].exits.has('000002'), 'active 无徽章');
    assert.equal(hiddenCodes.size, 0, '5 日内离场不进 hiddenCodes');
  }
});

// ================================================================
// 3. 起算日 = exit_date（打标签当天开始算），非 last_seen
// ================================================================
test('起算日为 exit_date：last_seen 较近但 exit_date 超期仍隐藏', () => {
  // exit_date=TD[1]（age=6 超期），last_seen=TD[5]（若按 last_seen 算 age=2 会误保留）
  const raw = trader('t1', '游资A', [
    rec(TD[0], '000001'),
    rec(TD[0], '000002'), // active 保卡
  ], [
    {
      code: '000001', name: '000001', status: 'exited_confirmed',
      exit_type: 'sell_cover', last_seen: TD[5], exit_date: TD[1],
      first_seen: TD[0], buy_total: 2e8, sell_total: 1e8, net_total: 1e8,
    },
    pos('000002', 'active', null),
  ]);
  const { traders, hiddenCodes } = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  assert.ok(!traders[0].records.some((r) => r.code === '000001'), '按 exit_date 起算 age=6 应隐藏');
  assert.ok(traders[0].records.some((r) => r.code === '000002'), 'active 保留');
  assert.ok(hiddenCodes.has('000001'));

  // 反向：exit_date=TD[2]（age=5 保留）但 last_seen 更早 TD[0] → 仍按 exit_date 展示
  const raw2 = trader('t2', '游资B', [
    rec(TD[0], '000002'),
  ], [
    {
      code: '000002', name: '000002', status: 'exited_probable',
      exit_type: 'silent', last_seen: TD[0], exit_date: TD[2],
      first_seen: TD[0], buy_total: 2e8, sell_total: 1e8, net_total: 1e8,
    },
  ]);
  const r2 = buildViewTraders([raw2], TD, AS_OF, { includeExpired: false });
  assert.ok(r2.traders[0].records.some((r) => r.code === '000002'), '按 exit_date 起算 age=5 应展示');
  assert.ok(r2.traders[0].exits.has('000002'));
});

// ================================================================
// 4. 每日动向 / 个股游资总览口径：includeExpired=true 全量展示 + 全徽章
// ================================================================
test('每日动向/个股总览口径：includeExpired=true 时超期离场配对也全量显示带徽章', () => {
  const exitDate6 = TD[7 - 6]; // age=6 超期
  const raw = trader('t1', '游资A', [
    rec(TD[0], 'A001'), // 超期离场 → 全量视图也应显示
    rec(TD[0], 'A002'), // active
  ], [
    pos('A001', 'exited_confirmed', exitDate6),
    pos('A002', 'active', null),
  ]);
  const { traders, hiddenCodes } = buildViewTraders([raw], TD, AS_OF, { includeExpired: true });
  assert.equal(traders.length, 1, '全量视图整卡不消失');
  assert.ok(traders[0].records.some((r) => r.code === 'A001'), '超期离场配对应显示');
  assert.ok(traders[0].exits.has('A001'), '超期离场配对应带徽章');
  assert.ok(traders[0].records.some((r) => r.code === 'A002'));
  // hiddenCodes 恒表示「默认隐藏集」（与开关无关的设计语义），全量视图调用方不消费
  assert.ok(hiddenCodes.has('A001'));
});

// ================================================================
// 5. 三种离场状态同等处理
// ================================================================
test('三种离场状态（已清仓/疑似清仓/已离场）同等处理', () => {
  const exitDate = TD[7 - 6]; // age=6
  const raw = trader('t1', '游资A', [
    rec(TD[0], 'A001'), rec(TD[0], 'A002'), rec(TD[0], 'A003'),
  ], [
    pos('A001', 'exited_confirmed', exitDate),
    pos('A002', 'exited_probable', exitDate),
    pos('A003', 'exited_forced', exitDate),
  ]);
  const { hiddenCodes } = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  assert.deepEqual([...hiddenCodes].sort(), ['A001', 'A002', 'A003']);
});

// ================================================================
// 6. active 始终展示、无 positions 的旧产物保守展示
// ================================================================
test('active 始终展示，无 positions 字段的旧产物保守展示', () => {
  const raw = trader('t1', '游资A', [
    rec(TD[0], '000001'),
  ], [
    pos('000001', 'active', null),
  ]);
  const { traders, hiddenCodes } = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  assert.equal(traders.length, 1);
  assert.ok(traders[0].records.some((r) => r.code === '000001'));
  assert.equal(hiddenCodes.size, 0);

  // 无 positions
  const rawNoPos = trader('t1', '游资A', [rec(TD[0], '000002')]);
  const r2 = buildViewTraders([rawNoPos], TD, AS_OF, { includeExpired: false });
  assert.equal(r2.traders.length, 1);
  assert.equal(r2.hiddenCodes.size, 0);
});

// ================================================================
// 7. exit_date 不在序列 / asOf 缺失 → 保守展示 + 徽章
// ================================================================
test('exit_date 不在交易日序列或 asOf 缺失时保守展示并带徽章', () => {
  const raw = trader('t1', '游资A', [
    rec(TD[0], '000001'),
  ], [
    pos('000001', 'exited_confirmed', '2099-01-01'), // 不在序列
  ]);
  const { traders, hiddenCodes } = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  assert.equal(traders.length, 1);
  assert.ok(traders[0].records.some((r) => r.code === '000001'), '日期异常保守展示');
  assert.ok(traders[0].exits.has('000001'), '日期异常时按已离场状态展示徽章');
  assert.equal(hiddenCodes.size, 0, '异常日期不进隐藏集');

  // asOf = null
  const { traders: t2 } = buildViewTraders([raw], TD, null, { includeExpired: false });
  assert.equal(t2.length, 1);
  assert.ok(t2[0].exits.has('000001'));
});

// ================================================================
// 8. includeExpired 切换后 hiddenCodes 不变（恒表示默认隐藏集）
// ================================================================
test('hiddenCodes 在 includeExpired 切换前后保持一致', () => {
  const exitDate6 = TD[7 - 6]; // age=6
  const raw = trader('t1', '游资A', [
    rec(TD[0], '000001'),
  ], [
    pos('000001', 'exited_confirmed', exitDate6),
  ]);
  const r1 = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  const r2 = buildViewTraders([raw], TD, AS_OF, { includeExpired: true });
  assert.deepEqual([...r1.hiddenCodes], [...r2.hiddenCodes]);
});

// ================================================================
// 9. asOf 前移一日后计数更新
// ================================================================
test('asOf 前移一日后 hiddenCodes 更新', () => {
  // exit_date = 2026-11-04 (TD[1]):
  //   age from AS_OF(7) = 6 → 隐藏
  //   age from TD[6] = 5 → 不隐藏
  const raw = trader('t1', '游资A', [
    rec(TD[0], '000001'),
  ], [
    pos('000001', 'exited_confirmed', TD[1]),
  ]);
  const r1 = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  assert.ok(r1.hiddenCodes.has('000001'), 'asOf=2026-11-13 时 age=6 应隐藏');

  const r2 = buildViewTraders([raw], TD, TD[6], { includeExpired: false });
  assert.ok(!r2.hiddenCodes.has('000001'), 'asOf=2026-11-12 时 age=5 应不隐藏');
});

// ================================================================
// 10. 混合场景：同游资既有 age≤5 配对也有 age≥6 配对
// ================================================================
test('混合场景：同游资既有 age≤5 配对也有 age≥6 配对', () => {
  const raw = trader('t1', '游资A', [
    rec(TD[0], '000001'), // age=6 → 隐藏
    rec(TD[2], '000002'), // age=5 → 展示
    rec(TD[3], '000003'), // age=4 → 展示
  ], [
    pos('000001', 'exited_confirmed', TD[1]),
    pos('000002', 'exited_probable', TD[2]),
    pos('000003', 'exited_forced', TD[3]),
  ]);
  const { traders, hiddenCodes } = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  assert.equal(traders.length, 1);
  const t = traders[0];
  assert.ok(!t.records.some((r) => r.code === '000001'), 'age=6 应隐藏');
  assert.ok(t.records.some((r) => r.code === '000002'), 'age=5 应展示');
  assert.ok(t.records.some((r) => r.code === '000003'), 'age=4 应展示');
  assert.deepEqual([...hiddenCodes], ['000001']);
});

// ================================================================
// 11. 整卡隐藏分支：全部配对超期 → includeExpired=false 时卡片消失
// ================================================================
test('整卡隐藏：全部配对超期且 includeExpired=false → 卡片消失', () => {
  const raw = trader('t1', '游资A', [
    rec(TD[0], '000001'),
  ], [
    pos('000001', 'exited_confirmed', TD[1]), // age=6
  ]);
  const { traders } = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  assert.equal(traders.length, 0);

  // includeExpired=true → 卡片回来
  const { traders: t2 } = buildViewTraders([raw], TD, AS_OF, { includeExpired: true });
  assert.equal(t2.length, 1);
});

// ================================================================
// 12. stats 口径与展示同步：隐藏的记录不参与统计，回显后恢复
// ================================================================
test('stats 同步：隐藏配对不参与统计，回显后同步恢复', () => {
  const raw = trader('t1', '游资A', [
    rec(TD[0], 'A001', '甲', 1e8),
    rec(TD[0], 'A002', '乙', 2e8),
  ], [
    pos('A001', 'exited_confirmed', TD[1]), // age=6 → 隐藏
    pos('A002', 'active', null),
  ]);
  // 默认隐藏
  const r1 = buildViewTraders([raw], TD, AS_OF, { includeExpired: false });
  assert.equal(r1.traders[0].stats.records, 1, '隐藏后只统计 A002');
  assert.equal(r1.traders[0].stats.net_total, 2e8);

  // 回显
  const r2 = buildViewTraders([raw], TD, AS_OF, { includeExpired: true });
  assert.equal(r2.traders[0].stats.records, 2, '回显后两条都统计');
  assert.equal(r2.traders[0].stats.net_total, 3e8);
});

// ================================================================
// 13. exitAgeInTradingDays 直接测试（按 exit_date 起算）
// ================================================================
test('exitAgeInTradingDays：正常值、边界、异常', () => {
  assert.equal(exitAgeInTradingDays(
    { exit_date: TD[3] } as LhbPosition, TD, AS_OF,
  ), 4);
  assert.equal(exitAgeInTradingDays(
    { exit_date: AS_OF } as LhbPosition, TD, AS_OF,
  ), 0);
  assert.equal(exitAgeInTradingDays(
    { exit_date: '2099-01-01' } as LhbPosition, TD, AS_OF,
  ), null);
  assert.equal(exitAgeInTradingDays(
    { exit_date: TD[3] } as LhbPosition, TD, null,
  ), null);
  // j < i 的情形
  assert.equal(exitAgeInTradingDays(
    { exit_date: TD[7] } as LhbPosition, TD, TD[3],
  ), null);
  // exit_date 缺失（异常产物）→ null
  assert.equal(exitAgeInTradingDays(
    { last_seen: TD[3] } as LhbPosition, TD, AS_OF,
  ), null);
});
