import type { ComponentType, SVGProps } from 'react';
import { overallAccuracy, useMarketData } from '../data/MarketDataProvider';
import { EmptyState, IndicatorHeader, LayerDots, Notice, SignalBadge } from '../components/ui';
import { SignalTable } from '../components/SignalTable';
import { LAYER_LABELS } from '../data/types';

export interface IndicatorPageConfig {
  title: string;
  subtitle: string;
  gradientClass: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}

const MECHANISM = [
  { layer: '价格确认', detail: '收盘价突破候选日最高价（底部）或跌破候选日最低价（顶部）' },
  { layer: '动量确认', detail: 'K 线方向与 RSI 转向一致；顶部额外要求 MA5 已掉头向下' },
  { layer: '量能确认', detail: '成交量 ≥ 1.2 倍 5 日均量，或横截面宽度改善 ≥ 5 个百分点' },
];

/**
 * 指标页通用模板。
 * 当前数据契约只提供确认型信号与三层状态，不提供单指标明细序列，
 * 因此这里展示真实的全市场统计与确认状态，并明确标注"明细待接入"。
 */
export function IndicatorPage({ config }: { config: IndicatorPageConfig }) {
  const { snapshot } = useMarketData();
  const accuracy = overallAccuracy(snapshot);
  const legacyTriggers = snapshot?.indices.reduce(
    (sum, item) => sum + (item.accuracy?.bottom?.triggers ?? 0) + (item.accuracy?.top?.triggers ?? 0),
    0,
  ) ?? 0;

  if (!snapshot || snapshot.data_state === 'preparing') {
    return (
      <div className="p-8">
        <IndicatorHeader
          icon={config.icon}
          gradientClass={config.gradientClass}
          title={config.title}
          subtitle={config.subtitle}
        />
        <div className="glass-card rounded-2xl p-12 text-center">
          <p className="text-slate-500">{snapshot?.progress?.detail || '正在加载数据…'}</p>
        </div>
      </div>
    );
  }

  const asOf = snapshot.as_of;
  const todayConfirmed = snapshot.recent_signals.filter((row) => row.date === asOf).length;
  const pending = snapshot.candidates.length;

  return (
    <div className="p-8">
      <IndicatorHeader
        icon={config.icon}
        gradientClass={config.gradientClass}
        title={config.title}
        subtitle={config.subtitle}
        extra={
          <>
            <span className="inline-flex items-center px-3 py-1.5 rounded-full text-sm font-medium bg-mint-50 text-mint-700">
              <span className="signal-dot confirm"></span>当日确认 {todayConfirmed}
            </span>
            <span className="inline-flex items-center px-3 py-1.5 rounded-full text-sm font-medium bg-coral-50 text-coral-600">
              <span className="signal-dot pending"></span>待确认 {pending}
            </span>
          </>
        }
      />

      <div className="mb-6">
        <Notice tone="warn">
          本页尚未接入该指标的明细序列（单指标 K 线与逐日因子值）。当前展示的是确认型信号体系下的真实汇总数据，
          不包含任何占位数值；明细计算需在数据契约扩展后单独实现。
        </Notice>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
        <div className="lg:col-span-2 glass-card rounded-2xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h4 className="font-bold text-slate-800">最近确认信号</h4>
            <span className="text-xs text-slate-400">
              {snapshot.mode === 'live' ? '盘中临时态，收盘后重算' : `收盘数据 ${asOf ?? ''}`}
            </span>
          </div>
          <SignalTable
            rows={snapshot.recent_signals}
            asOf={asOf}
            provisional={snapshot.provisional}
            limit={10}
          />
        </div>

        <div className="space-y-6">
          <div className="glass-card rounded-2xl p-6">
            <h4 className="font-bold text-slate-800 mb-4">确认机制</h4>
            <div className="space-y-3">
              {MECHANISM.map((row, index) => (
                <div
                  key={row.layer}
                  className={`flex gap-3 py-2 ${index === MECHANISM.length - 1 ? '' : 'border-b border-slate-50'}`}
                >
                  <span className="w-5 h-5 shrink-0 rounded-full bg-brand-50 text-brand-700 text-[11px] font-bold flex items-center justify-center">
                    {index + 1}
                  </span>
                  <div>
                    <div className="text-sm font-medium text-slate-700">{row.layer}</div>
                    <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">{row.detail}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="glass-card rounded-2xl p-6">
            <h4 className="font-bold text-slate-800 mb-4">统计概览</h4>
            <div className="grid grid-cols-2 gap-4">
              <StatTile
                value={accuracy.rate === null ? '--' : `${accuracy.rate.toFixed(1)}%`}
                label={`历史基线（旧口径）· 触发 ${legacyTriggers} 条 / 已判定 ${accuracy.resolved}`}
                tone="brand"
              />
              <StatTile
                value={accuracy.avgDrawdown === null ? '--' : `${(accuracy.avgDrawdown * 100).toFixed(1)}%`}
                label="平均最大回撤"
                tone="coral"
              />
              <StatTile
                value={
                  accuracy.avgForwardReturn20 === null
                    ? '--'
                    : `${(accuracy.avgForwardReturn20 * 100).toFixed(1)}%`
                }
                label="平均 20 日收益"
                tone="mint"
              />
              <StatTile
                value={`${snapshot.indices.reduce((sum, item) => sum + item.signals.length, 0)}`}
                label="历史确认信号"
                tone="ocean"
              />
              <StatTile value={`${todayConfirmed}`} label="当日确认" tone="mint" />
              <StatTile value={`${pending}`} label="待确认候选" tone="coral" />
            </div>
            <p className="text-[11px] text-slate-400 mt-3 leading-relaxed">
              {accuracy.start && accuracy.end
                ? `统计区间 ${accuracy.start} ~ ${accuracy.end}；触发 ${legacyTriggers} 条、已判定 ${accuracy.resolved} 条、未决 ${legacyTriggers - accuracy.resolved} 条。`
                : '暂无已判定样本。'}
              {' '}上面的百分比是历史基线，来自旧「任意时点首次均线反转」口径：没有持有期上限、没有浮亏约束、未决不进分母，
              与【指标总览】的多持有期准确率矩阵不可直接比较，也不是交易收益率。
              {snapshot.precision?.legacy_rate_note ? ` ${snapshot.precision.legacy_rate_note}。` : ''}
            </p>
          </div>
        </div>
      </div>

      <div className="glass-card rounded-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h4 className="font-bold text-slate-800">三层确认进度</h4>
          <span className="text-xs text-slate-400">价格 → 动量 → 量能</span>
        </div>
        {snapshot.candidates.length === 0 ? (
          <EmptyState text="当前没有处于确认窗口内的候选" hint="候选触发后需 1-3 个交易日观察" />
        ) : (
          <div className="space-y-3">
            {snapshot.candidates.map((row) => (
              <div
                key={`${row.code}-${row.date}`}
                className="flex items-center gap-4 p-4 bg-slate-50/60 rounded-xl border border-slate-100"
              >
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-800">{row.name ?? row.code}</span>
                    <SignalBadge type={row.type} size="sm" />
                    <span className="text-xs text-slate-500">触发 {row.date}</span>
                  </div>
                  <p className="text-xs text-slate-500 mt-1">
                    {LAYER_LABELS.map(({ key, label }) =>
                      `${label}${row.layers[key] ? '✓' : '×'}`,
                    ).join('  ')}
                  </p>
                </div>
                <LayerDots layers={row.layers} passed={row.passed} />
                <span className="text-xs text-slate-400">已等待 {row.trading_days_since} 日</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const toneMap = {
  brand: 'bg-brand-50 text-brand-600',
  ocean: 'bg-ocean-50 text-ocean-600',
  mint: 'bg-mint-50 text-mint-600',
  coral: 'bg-coral-50 text-coral-500',
} as const;

function StatTile({ value, label, tone }: { value: string; label: string; tone: keyof typeof toneMap }) {
  const [bg, text] = toneMap[tone].split(' ');
  return (
    <div className={`text-center p-3 ${bg} rounded-xl`}>
      <div className={`text-2xl font-bold ${text}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-1">{label}</div>
    </div>
  );
}
