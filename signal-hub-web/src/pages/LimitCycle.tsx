/**
 * 连板周期页（设计稿版本 2 布局，客观数据口径）。
 *
 * 展示约束（用户 2026-11-13 确认）：
 * - 所有数值来自 /api/limit-cycle 产物，页面不生成、不推算、不补齐；
 * - 相位曲线只画 phase.observed（已发生交易日）的真实最高板数，
 *   不设阶段阈值、不做阶段归类、不画未来示意段；
 * - 晋级率缺样本量或统计区间时不展示比率；
 * - 数据源、晋级率、数据覆盖率默认收起（统一折叠区），比率仍随附 n 与统计区间；
 * - 今日连板个股表格固定 300px，本页色板为局部配色；
 * - 两张折线图悬停提示只索引产物已有的观测点，不插值、不推算、不补点。
 */
import { useState, type MouseEvent } from 'react';
import { EmptyState, Notice } from '../components/ui';
import { useLimitCycle } from '../hooks/useLimitCycle';
import {
  BOARD_COLORS, LIMIT_CYCLE_STAGES,
  type LimitCycleCoverage, type LimitCycleMetrics, type LimitCyclePhase,
  type LimitCycleTemperature, type LimitCycleLadderRow, type LimitCycleStock,
} from '../data/limitCycle';

/**
 * 折线图悬停：把鼠标位置换算到 viewBox 坐标后吸附到「最近的真实数据点」。
 * 只索引已有观测值，不插值、不推算、不补点，避免产生产物里不存在的数值。
 */
function useNearestHover(viewWidth: number, xs: number[]) {
  const [active, setActive] = useState<number | null>(null);
  const handleMove = (event: MouseEvent<SVGSVGElement>) => {
    if (!xs.length) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    const viewX = ((event.clientX - rect.left) / rect.width) * viewWidth;
    let best = 0;
    let bestDist = Number.POSITIVE_INFINITY;
    for (let i = 0; i < xs.length; i += 1) {
      const dist = Math.abs(xs[i] - viewX);
      if (dist < bestDist) {
        bestDist = dist;
        best = i;
      }
    }
    setActive(best);
  };
  const clear = () => setActive(null);
  return { active, handleMove, clear };
}

/** 与折线图共用同一 viewBox 坐标系的 HTML 浮层；pointer-events-none 避免遮挡鼠标导致抖动。 */
function ChartTooltip({ x, y, viewWidth, viewHeight, accent, metric, unit, value, date }: {
  x: number;
  y: number;
  viewWidth: number;
  viewHeight: number;
  accent: string;
  metric: string;
  unit: string;
  value: number;
  date: string;
}) {
  const left = Math.max(7, Math.min(93, (x / viewWidth) * 100));
  const below = y < 44;
  return (
    <div
      role="status"
      className="pointer-events-none absolute z-20 whitespace-nowrap rounded-lg border border-slate-200 bg-white/95 px-3 py-2 shadow-md"
      style={{
        left: `${left}%`,
        top: `${(y / viewHeight) * 100}%`,
        transform: below ? 'translate(-50%, 12px)' : 'translate(-50%, calc(-100% - 12px))',
      }}
    >
      <div className="text-xs font-medium text-slate-700 tabular-nums">{date}</div>
      <div className="mt-1 flex items-center gap-1.5 text-xs text-slate-500">
        <span className="inline-block h-2 w-2 rounded-full" style={{ background: accent }} />
        {metric}
        <span className="font-semibold tabular-nums" style={{ color: accent }}>{value} {unit}</span>
      </div>
      <div className="mt-1 text-xs text-slate-400">已发生交易日 · 非预测值</div>
    </div>
  );
}

const WAVE_W = 1200;
const WAVE_H = 150;
const WAVE_X0 = 16;
const WAVE_X1 = WAVE_W - 16;
const WAVE_BASE = 112;
const WAVE_SPAN = 78;
const WAVE_ACCENT = '#3a9a6a';

/** 固定 y 轴上限映射：只用管道给定的 curve_max，不做样本归一化，避免未来函数。 */
function waveY(value: number, curveMax: number): number {
  const upper = curveMax > 0 ? curveMax : 10;
  return WAVE_BASE - (Math.max(0, Math.min(upper, value)) / upper) * WAVE_SPAN;
}

/** 相位卡：真实已发生数据曲线 + 仅作版式参考的五阶段色带。宽 viewBox + h-auto 铺满容器。 */
function PhaseWaveCard({ phase, ready }: { phase: LimitCyclePhase | null; ready: boolean }) {
  const stages = phase?.stages.length ? phase.stages : LIMIT_CYCLE_STAGES;
  const observed = phase?.observed ?? [];
  const curveMax = phase?.curve_max ?? 10;
  const segWidth = (WAVE_X1 - WAVE_X0) / stages.length;
  const points = observed.map((point, index) => {
    const x = observed.length === 1
      ? WAVE_X0
      : WAVE_X0 + (index / (observed.length - 1)) * (WAVE_X1 - WAVE_X0);
    return { x, y: waveY(point.value, curveMax), point };
  });
  const polyline = points.map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const last = points.length ? points[points.length - 1] : null;
  const readouts = phase?.readouts ?? [];
  const { active, handleMove, clear } = useNearestHover(WAVE_W, points.map(({ x }) => x));
  const activePoint = active !== null && active < points.length ? points[active] : null;

  return (
    <section className="glass-card rounded-2xl p-6 mb-6 fade-in">
      <div className="flex items-center justify-between gap-4 mb-2">
        <h3 className="text-base font-bold text-slate-800">
          当前周期相位 · {phase?.curve_metric ?? '每日最高连板高度'}
        </h3>
        <span className="text-xs text-slate-400 tabular-nums">
          {observed.length ? `${observed.length} 个有效交易日 · ${observed[0].date} ~ ${observed[observed.length - 1].date}` : '数据待接入'}
        </span>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${WAVE_W} ${WAVE_H}`} className="w-full h-auto block" role="img"
          aria-label="每日最高连板高度曲线，仅含已发生交易日；五阶段色带仅作版式参考；悬停可查看各交易日数值"
          onMouseMove={ready ? handleMove : undefined} onMouseLeave={clear}>
          {stages.map((_, index) => (
            <rect key={index} x={WAVE_X0 + index * segWidth + 2} y={12}
              width={segWidth - 4} height={104} rx={4} fill="#f7f9fa" />
          ))}
          <path d={`M${WAVE_X0} 34 H${WAVE_X1} M${WAVE_X0} 73 H${WAVE_X1}`}
            stroke="#eef2f4" strokeWidth={1} strokeDasharray="4 5" fill="none" />
          <path d={`M${WAVE_X0} ${WAVE_BASE} H${WAVE_X1}`} stroke="#e6edf0" strokeWidth={1} fill="none" />
          {ready && points.length > 1 && (
            <polyline points={polyline} fill="none" stroke="#3a9a6a" strokeWidth={2.4}
              strokeLinecap="round" strokeLinejoin="round" />
          )}
          {ready && points.map(({ x, y, point }) => (
            <circle key={point.date} cx={x} cy={y} r={2.8} fill="#3a9a6a" />
          ))}
          {ready && last && (
            <>
              <circle cx={last.x} cy={last.y} r={9} fill="none" stroke="#c0392b" strokeWidth={1.5} />
              <circle cx={last.x} cy={last.y} r={4.5} fill="#c0392b" />
              <text x={Math.min(last.x, WAVE_X1 - 30)} y={last.y < 26 ? last.y + 18 : last.y - 11}
                textAnchor="middle" fontSize={11} fill="#c0392b" fontWeight={500}>
                {last.point.value} {phase?.curve_unit ?? '板'}
              </text>
            </>
          )}
          {!ready && (
            <text x={WAVE_W / 2} y={72} textAnchor="middle" fontSize={12} fill="#94a3b8">
              等待连板周期数据接入，暂不绘制曲线
            </text>
          )}
          {stages.map((stage, index) => (
            <text key={stage} x={WAVE_X0 + (index + 0.5) * segWidth} y={140}
              textAnchor="middle" fontSize={11} fill="#94a3b8">
              {stage}
            </text>
          ))}
          {ready && activePoint && (
            <g>
              <line x1={activePoint.x} x2={activePoint.x} y1={12} y2={WAVE_BASE}
                stroke={WAVE_ACCENT} strokeWidth={1} strokeDasharray="3 3" opacity={0.6} />
              <circle cx={activePoint.x} cy={activePoint.y} r={5.5}
                fill="#ffffff" stroke={WAVE_ACCENT} strokeWidth={2.2} />
            </g>
          )}
        </svg>
        {ready && activePoint && (
          <ChartTooltip
            x={activePoint.x} y={activePoint.y}
            viewWidth={WAVE_W} viewHeight={WAVE_H}
            accent={WAVE_ACCENT}
            metric={phase?.curve_metric ?? '每日最高连板高度'}
            unit={phase?.curve_unit ?? '板'}
            value={activePoint.point.value}
            date={activePoint.point.date}
          />
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 mt-1">
        <span className="text-xs text-slate-400">
          曲线 = 已发生交易日的最高连板高度 · 五阶段色带仅作版式参考，不判定当前阶段、不预测后续走势 · 无阈值
        </span>
        <span className="text-xs text-slate-500">当日客观读数</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-4">
        {readouts.length ? readouts.map((item) => (
          <div key={item.label} className="rounded-lg bg-brand-50 px-3 py-2">
            <div className="text-sm font-medium text-slate-700">{item.value ?? '—'}</div>
            <div className="text-xs text-slate-400">
              {item.label}{item.sample ? ` · ${item.sample}` : ''}
            </div>
          </div>
        )) : (
          <div className="col-span-3 text-xs text-slate-400">当日读数待接入</div>
        )}
      </div>
    </section>
  );
}

/** 首屏只保留当日可观测的三个客观值；晋级率移至页首默认折叠区。 */
function MetricCards({ metrics }: { metrics: LimitCycleMetrics | null }) {
  const cards = [
    {
      label: '最高连板',
      value: metrics?.max_board != null ? `${metrics.max_board} 板` : '—',
      hint: metrics?.max_board_leader ? `龙头 ${metrics.max_board_leader}` : '—',
      color: '#c0392b',
    },
    {
      label: '连板家数',
      value: metrics?.continuous_count != null ? String(metrics.continuous_count) : '—',
      hint: metrics?.continuous_change != null
        ? `较上一有效交易日 ${metrics.continuous_change > 0 ? '+' : ''}${metrics.continuous_change}` : '—',
      color: '#334155',
    },
    {
      label: '涨停家数',
      value: metrics?.limit_up_count != null ? String(metrics.limit_up_count) : '—',
      hint: metrics?.broken_count != null ? `炸板 ${metrics.broken_count} 家` : '炸板家数未取得',
      color: '#334155',
    },
  ];
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
      {cards.map((card) => (
        <div key={card.label} className="glass-card rounded-2xl p-6 fade-in">
          <div className="text-sm font-medium text-slate-500 mb-2">{card.label}</div>
          <div className="text-2xl font-bold" style={{ color: card.color }}>{card.value}</div>
          <div className="text-xs text-slate-400 mt-2">{card.hint}</div>
        </div>
      ))}
    </div>
  );
}

const TEMP_W = 600;
const TEMP_H = 150;
const TEMP_X0 = 24;
const TEMP_X1 = TEMP_W - 24;
const TEMP_BASE = 118;
const TEMP_ACCENT = '#c0392b';

/** 固定 100 家上限做线性映射，只用已发生数据，不做样本归一化、不设阈值。 */
function tempY(value: number): number {
  return TEMP_BASE - (Math.max(0, Math.min(100, value)) / 100) * 92;
}

function TemperatureCard({ temperature }: { temperature: LimitCycleTemperature | null }) {
  const value = temperature?.value ?? null;
  const percentile = temperature?.percentile ?? null;
  const series = temperature?.series ?? [];
  const points = series.map((point, index) => ({
    x: series.length === 1
      ? TEMP_X0
      : TEMP_X0 + (index / (series.length - 1)) * (TEMP_X1 - TEMP_X0),
    y: tempY(point.value),
    point,
  }));
  const polyline = points.map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = points.length > 1
    ? `M${points[0].x.toFixed(1)} ${TEMP_BASE} `
      + points.map(({ x, y }) => `L${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
      + ` L${points[points.length - 1].x.toFixed(1)} ${TEMP_BASE} Z`
    : '';
  const last = points.length ? points[points.length - 1] : null;
  const { active, handleMove, clear } = useNearestHover(TEMP_W, points.map(({ x }) => x));
  const activePoint = active !== null && active < points.length ? points[active] : null;

  return (
    <section className="glass-card rounded-2xl p-6 fade-in">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-bold text-slate-800">情绪温度</h3>
        <span className="text-xs text-slate-400">{temperature?.metric ?? '当日涨停家数'} · 有效交易日</span>
      </div>
      <div className="flex items-baseline gap-3 mb-3">
        <span className="text-3xl font-bold" style={{ color: '#c0392b' }}>{value ?? '—'}</span>
        <span className="text-xs text-slate-400">{temperature?.unit ?? '家'}</span>
        {percentile !== null && (
          <span className="text-xs px-2 py-1 rounded-full" style={{ color: '#c0392b', background: '#fdf2ee' }}>
            分位 {percentile}%
          </span>
        )}
        <span className="text-xs text-slate-400 tabular-nums">
          {temperature?.change != null
            ? `较上一有效交易日 ${temperature.change > 0 ? '+' : ''}${temperature.change} ${temperature?.unit ?? '家'}`
            : '—'}
        </span>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${TEMP_W} ${TEMP_H}`} className="w-full h-auto block" role="img"
          aria-label="有效交易日涨停家数折线图，淡色网格线仅作刻度参考，不代表阈值；悬停可查看各交易日数值"
          onMouseMove={series.length ? handleMove : undefined} onMouseLeave={clear}>
          <path d={`M${TEMP_X0} 34 H${TEMP_X1} M${TEMP_X0} 70 H${TEMP_X1} M${TEMP_X0} 104 H${TEMP_X1}`}
            stroke="#eef2f4" strokeWidth={1} strokeDasharray="4 5" fill="none" />
          <path d={`M${TEMP_X0} ${TEMP_BASE} H${TEMP_X1}`} stroke="#e6edf0" strokeWidth={1} fill="none" />
          {area && <path d={area} fill="#fbe4dc" stroke="none" />}
          {points.length > 1 && (
            <polyline points={polyline} fill="none" stroke="#c0392b" strokeWidth={2}
              strokeLinecap="round" strokeLinejoin="round" />
          )}
          {points.map(({ x, y, point }) => (
            <circle key={point.date} cx={x} cy={y} r={2.3} fill="#c0392b" />
          ))}
          {last && (
            <>
              <circle cx={last.x} cy={last.y} r={7} fill="none" stroke="#c0392b" strokeWidth={1.3} />
              <text x={Math.min(last.x, TEMP_X1 - 20)} y={last.y < 24 ? last.y + 16 : last.y - 9}
                textAnchor="middle" fontSize={11} fill="#c0392b" fontWeight={500}>
                {last.point.value}
              </text>
            </>
          )}
          {series.length > 0 && (
            <>
              <text x={TEMP_X0} y={140} fontSize={11} fill="#94a3b8">{series[0].date}</text>
              <text x={TEMP_X1} y={140} textAnchor="end" fontSize={11} fill="#94a3b8">
                {series[series.length - 1].date}
              </text>
            </>
          )}
          {activePoint && (
            <g>
              <line x1={activePoint.x} x2={activePoint.x} y1={14} y2={TEMP_BASE}
                stroke={TEMP_ACCENT} strokeWidth={1} strokeDasharray="3 3" opacity={0.6} />
              <circle cx={activePoint.x} cy={activePoint.y} r={5.5}
                fill="#ffffff" stroke={TEMP_ACCENT} strokeWidth={2.2} />
            </g>
          )}
        </svg>
        {activePoint && (
          <ChartTooltip
            x={activePoint.x} y={activePoint.y}
            viewWidth={TEMP_W} viewHeight={TEMP_H}
            accent={TEMP_ACCENT}
            metric={temperature?.metric ?? '当日涨停家数'}
            unit={temperature?.unit ?? '家'}
            value={activePoint.point.value}
            date={activePoint.point.date}
          />
        )}
      </div>
      <p className="text-xs text-slate-400 mt-2">
        {temperature?.formula ?? '指标口径由数据管道提供。'} 网格线仅作刻度参考，不代表阈值。
      </p>
    </section>
  );
}

function LadderCard({ ladder }: { ladder: LimitCycleLadderRow[] }) {
  const maxCount = Math.max(1, ...ladder.map((row) => row.count));
  return (
    <section className="glass-card rounded-2xl p-6 fade-in">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-bold text-slate-800">连板梯队</h3>
        <span className="text-xs text-slate-400">按板级排列</span>
      </div>
      {ladder.length ? (
        <div className="flex flex-col gap-2.5">
          {[...ladder].sort((a, b) => b.board - a.board).map((row) => (
            <div key={row.board} className="flex items-center gap-3">
              <span className="w-10 text-xs text-slate-400 shrink-0">{row.board === 1 ? '首板' : `${row.board} 板`}</span>
              <div className="flex-1 h-2.5 rounded-full bg-slate-100">
                <div className="h-2.5 rounded-full"
                  style={{
                    width: `${(row.count / maxCount) * 100}%`,
                    background: BOARD_COLORS[Math.min(BOARD_COLORS.length - 1, Math.max(0, row.board - 1))],
                  }} />
              </div>
              <span className="w-8 text-right text-xs tabular-nums text-slate-600">{row.count}</span>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState text="连板梯队数据待接入" hint="条长对应各板级家数，数据到位后自动渲染" />
      )}
      <p className="text-xs text-slate-400 mt-4">条长对应家数 · 晋级率与样本量见页首「数据源 · 晋级率 · 数据覆盖率」折叠区</p>
    </section>
  );
}

function StocksCard({ stocks }: { stocks: LimitCycleStock[] }) {
  return (
    <section className="glass-card rounded-2xl p-6 fade-in">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-bold text-slate-800">今日连板个股</h3>
        <span className="text-xs text-slate-400">按连板高度排序 · 共 {stocks.length} 家</span>
      </div>
      <div className="overflow-auto" style={{ height: 300 }}>
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-white">
            <tr className="text-left text-xs text-slate-400 border-b border-slate-100">
              <th className="py-2 font-normal bg-white">代码</th>
              <th className="py-2 font-normal bg-white">名称</th>
              <th className="py-2 font-normal bg-white">连板</th>
              <th className="py-2 font-normal bg-white">所属板块</th>
              <th className="py-2 font-normal bg-white">封单(万)</th>
              <th className="py-2 font-normal bg-white">首封</th>
              <th className="py-2 font-normal bg-white">状态</th>
            </tr>
          </thead>
          <tbody>
            {stocks.length ? stocks.map((stock, index) => (
              <tr key={`${stock.code}-${index}`} className="border-b border-slate-50 hover:bg-slate-50/50">
                <td className="py-2.5 tabular-nums text-slate-600">{stock.code}</td>
                <td className="py-2.5 text-slate-700">{stock.name}</td>
                <td className="py-2.5 font-medium" style={{ color: stock.boards >= 5 ? '#c0392b' : '#334155' }}>
                  {stock.boards === 1 ? '首板' : `${stock.boards} 板`}
                </td>
                <td className="py-2.5 text-slate-600">{stock.sector ?? '—'}</td>
                <td className="py-2.5 tabular-nums text-slate-600">
                  {stock.seal_amount != null ? stock.seal_amount.toLocaleString('zh-CN') : '—'}
                </td>
                <td className="py-2.5 tabular-nums text-slate-600">{stock.first_seal ?? '—'}</td>
                <td className="py-2.5 text-slate-600">{stock.status ?? '—'}</td>
              </tr>
            )) : (
              <tr>
                <td colSpan={7} className="py-6 text-center text-xs text-slate-400">
                  暂无连板个股数据 · 页面不展示演示数值
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * 数据源 / 晋级率 / 数据覆盖率统一默认收起，避免长段口径文字与统计指标占据首屏。
 * 展开后晋级率仍完整随附样本量 n 与统计区间，口径不因折叠而简化。
 */
function CoverageDetails({ methodology, coverage, metrics, asOf }: {
  methodology: string | null;
  coverage: LimitCycleCoverage | null;
  metrics: LimitCycleMetrics | null;
  asOf: string | null;
}) {
  const hasRateBase = metrics?.promote_rate != null && metrics?.promote_sample != null;
  const rateText = hasRateBase
    ? `${metrics!.promote_rate!.toFixed(1)}% · 样本量 n=${metrics!.promote_sample} · 统计区间 ${metrics?.promote_window ?? '未标注'}`
    : '样本量或统计区间缺失，暂不展示比率（不补 0、不插值）。';
  return (
    <details className="group glass-card rounded-xl px-4 py-3 mb-6">
      <summary className="list-none cursor-pointer flex items-center justify-between gap-3 text-xs text-slate-500">
        <span>
          数据源 · 晋级率 · 数据覆盖率（默认收起）{asOf ? ` · 截至 ${asOf}` : ''}
        </span>
        <span className="shrink-0 text-brand-600">
          <span className="group-open:hidden">展开详情</span>
          <span className="hidden group-open:inline">收起</span>
        </span>
      </summary>
      <div className="mt-3 pt-3 border-t border-slate-100 text-xs leading-relaxed text-slate-500 space-y-2">
        <p>
          <span className="font-medium text-slate-600">数据源与统计口径 · </span>
          {methodology ?? '统计口径说明待数据管道提供。'}
        </p>
        <p>
          <span className="font-medium text-slate-600">2→3 晋级率 · </span>
          {rateText}
        </p>
        {coverage && (
          <p>
            <span className="font-medium text-slate-600">数据覆盖率 · </span>
            {coverage.valid_days}/{coverage.requested_days} 个交易日 · {coverage.start} ~ {coverage.end}
            {' · '}{coverage.note ?? ''} 所有比率均随附样本量与统计区间。
          </p>
        )}
      </div>
    </details>
  );
}

export function LimitCycle() {
  const { data, error, isLoading, refresh } = useLimitCycle();
  const ready = data?.data_state === 'ready';

  return (
    <div className="p-8 fade-in">
      <div className="flex items-center justify-between gap-4 mb-6">
        <p className="text-sm text-slate-500">情绪周期 · 连板梯队 · 今日连板个股</p>
        <div className="flex items-center gap-3">
          {data?.as_of && <span className="text-xs text-slate-400 tabular-nums">数据截至 {data.as_of}</span>}
          {isLoading && <span className="text-xs text-slate-400">加载中…</span>}
          <button type="button" onClick={refresh}
            className="px-4 py-2 rounded-lg bg-brand-50 text-brand-700 text-sm font-medium hover:bg-brand-100">
            刷新
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-6">
          <Notice tone="warn">
            {error.includes('接口不存在') || error.includes('404')
              ? '数据服务尚未加载连板周期接口（通常是服务在本页代码更新前启动）。请关闭当前服务窗口，重新运行「启动.bat」，然后刷新本页。'
              : `连板周期数据读取失败：${error}。可点击「刷新」重试。`}
          </Notice>
        </div>
      )}

      {ready ? (
        <CoverageDetails methodology={data?.methodology ?? null} coverage={data?.coverage ?? null}
          metrics={data?.metrics ?? null} asOf={data?.as_of ?? null} />
      ) : (
        <div className="mb-6">
          <Notice tone="warn">
            连板周期产物尚未生成或暂不可读。请在项目根目录执行
            「.venv\Scripts\python.exe pipeline/limit_cycle.py」生成 data/limit-cycle.json 后刷新；
            本页不展示演示数值。
          </Notice>
        </div>
      )}

      <PhaseWaveCard phase={data?.phase ?? null} ready={ready} />
      <MetricCards metrics={data?.metrics ?? null} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <TemperatureCard temperature={data?.temperature ?? null} />
        <LadderCard ladder={data?.ladder ?? []} />
      </div>
      <StocksCard stocks={data?.stocks ?? []} />
    </div>
  );
}
