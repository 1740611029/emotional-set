import test from 'node:test';
import assert from 'node:assert/strict';
import {
  HORIZONS,
  buildEvaluationViews,
  formatInterval,
  formatRate,
  formatSamples,
  groupThousands,
} from '../src/lib/sectorEvaluation.ts';
import { STATE_ORDER } from '../src/lib/sectorStates.ts';
import type {
  SectorEvaluation,
  SectorEvaluationSegment,
  SectorState,
  SectorStateEvaluation,
} from '../src/data/types.ts';

const STATES: SectorState[] = ['bull', 'warn', 'bear', 'rise'];

function cell(samples: number, rate: number | null = 0.5, smallSample = samples < 30): SectorStateEvaluation {
  return {
    entries: samples,
    samples,
    successes: Math.round(samples / 2),
    pending: 0,
    rate,
    wilson95: rate === null ? [null, null] : [rate - 0.02, rate + 0.02],
    small_sample: smallSample,
    note: smallSample ? '样本量较小，仅供参考' : null,
  };
}

function segment(
  name: SectorEvaluationSegment['segment'],
  cells: Record<SectorState, [SectorStateEvaluation | null, SectorStateEvaluation | null]>,
): SectorEvaluationSegment {
  return {
    segment: name,
    independent: false,
    states: Object.fromEntries(
      STATES.map((state) => [state, { '10': cells[state][0], '20': cells[state][1] }]),
    ) as Record<SectorState, Record<'10' | '20', SectorStateEvaluation | null>>,
  };
}

function evaluation(segments: SectorEvaluationSegment[] = [
  segment('research', {
    bull: [cell(1404, 0.5214), cell(1398, 0.4821)],
    warn: [cell(1128, 0.539), cell(1108, 0.5433)],
    bear: [cell(1359, 0.5342), cell(1359, 0.5342)],
    rise: [cell(1184, 0.5084), cell(1184, 0.5304)],
  }),
  segment('exploratory_validation', {
    bull: [cell(684, 0.5775), cell(668, 0.6138)],
    warn: [cell(565, 0.5062), cell(544, 0.5129)],
    bear: [cell(494, 0.4312), cell(468, 0.3932)],
    rise: [cell(441, 0.7211), cell(440, 0.7432)],
  }),
]): SectorEvaluation {
  return {
    algorithm_version: 'sector-donchian20-ma60-v2.0-em-source',
    research_status: 'post_hoc_exploratory_not_independent',
    rate_unit: 'fraction',
    boundary_policy: 'strict_segment_end_inclusive',
    note: '板块趋势为描述性分类，方向胜率不代表交易收益。',
    segments,
  };
}

test('buildEvaluationViews 两段 × 四态 × 两个窗口齐备', () => {
  const views = buildEvaluationViews(evaluation());
  assert.deepEqual(views.map((view) => view.label), ['研究段', '验证段']);
  for (const view of views) {
    assert.deepEqual(view.rows.map((row) => row.state), STATE_ORDER);
    assert.equal(view.independent, false);
    for (const row of view.rows) {
      assert.deepEqual(row.cells.map((item) => item.horizon), HORIZONS);
      assert.equal(row.cells.length, 2);
    }
  }
  const bull = views[0].rows.find((row) => row.state === 'bull')!;
  assert.equal(bull.label, '多头');
  assert.equal(bull.cells[0].rateText, '52.14%');
  assert.equal(bull.cells[0].sampleText, 'n=1,404');
  assert.equal(bull.cells[0].intervalText, '50.14%–54.14%');
});

test('无样本的格子显示占位而不是 0%', () => {
  const views = buildEvaluationViews(evaluation([
    segment('research', {
      bull: [cell(40), null],
      warn: [cell(0, null), cell(40)],
      bear: [cell(40), cell(40)],
      rise: [cell(40), cell(40)],
    }),
  ]));
  const rows = Object.fromEntries(views[0].rows.map((row) => [row.state, row]));
  assert.deepEqual(views[0].rows.map((row) => row.state), STATE_ORDER);
  assert.equal(rows.bull.cells[1].hasSamples, false);
  assert.equal(rows.bull.cells[1].rateText, '—');
  assert.equal(rows.bull.cells[1].intervalText, '—');
  assert.equal(rows.bull.cells[1].sampleText, 'n=0');
  assert.equal(rows.warn.cells[0].hasSamples, false);
  assert.equal(rows.warn.cells[0].rateText, '—');
  assert.equal(rows.warn.cells[0].sampleText, 'n=0');
  assert.equal(rows.warn.cells[1].hasSamples, true);
  assert.equal(rows.rise.cells[0].hasSamples, true);
});

test('小样本标记如实透传', () => {
  const views = buildEvaluationViews(evaluation([
    segment('research', {
      bull: [cell(12), cell(1400, 0.5, false)],
      warn: [cell(1400, 0.5, false), cell(1400, 0.5, false)],
      bear: [cell(1400, 0.5, false), cell(1400, 0.5, false)],
      rise: [cell(1400, 0.5, false), cell(1400, 0.5, false)],
    }),
  ]));
  assert.equal(views[0].rows[0].cells[0].smallSample, true);
  assert.equal(views[0].rows[0].cells[1].smallSample, false);
});

test('胜率与区间按百分数展示，样本量带千分位', () => {
  assert.equal(formatRate(0.7432), '74.32%');
  assert.equal(formatRate(null), '—');
  assert.equal(formatInterval([0.6775, 0.7609]), '67.75%–76.09%');
  assert.equal(formatInterval([null, null]), '—');
  assert.equal(groupThousands(5075), '5,075');
  assert.equal(formatSamples(120), 'n=120');
});

test('评估未生成或结构为空时返回空数组', () => {
  assert.deepEqual(buildEvaluationViews(null), []);
  assert.deepEqual(buildEvaluationViews(undefined), []);
  assert.deepEqual(buildEvaluationViews(evaluation([])), []);
});
