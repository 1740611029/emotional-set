import type { ComponentType, ReactNode, SVGProps } from 'react';
import type { CandidateLayers, SignalKind, SignalType } from '../data/types';
import { LAYER_LABELS } from '../data/types';

/** 信号类型标签：bottom=底部/买入（薄荷绿），top=顶部/卖出（珊瑚橙），none=中性 */
export function SignalBadge({ type, size = 'md' }: { type: SignalType; size?: 'sm' | 'md' }) {
  const meta = {
    bottom: { label: '底部', cls: 'bg-mint-50 text-mint-700', dot: 'confirm' },
    top: { label: '顶部', cls: 'bg-coral-50 text-coral-600', dot: 'pending' },
    none: { label: '无信号', cls: 'bg-slate-100 text-slate-500', dot: 'neutral' },
  }[type];
  const pad = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-xs';
  return (
    <span className={`inline-flex items-center rounded-full font-medium ${pad} ${meta.cls}`}>
      <span className={`signal-dot ${meta.dot}`}></span>
      {meta.label}
    </span>
  );
}

/** 三层确认进度点：价格 → 动量 → 量能 */
export function LayerDots({ layers, passed }: { layers: CandidateLayers; passed: number }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="inline-flex gap-1">
        {LAYER_LABELS.map(({ key, label }) => (
          <span
            key={key}
            title={`${label}确认${layers[key] ? '已通过' : '未通过'}`}
            className={`w-2 h-2 rounded-full ${layers[key] ? 'bg-brand-500' : 'bg-slate-200'}`}
          />
        ))}
      </span>
      <span className="text-xs text-slate-500">{passed}/3 层</span>
    </span>
  );
}

/** 涨跌幅文本：沿用项目色彩系统（薄荷绿上涨 / 珊瑚橙下跌） */
export function ChangeText({ value, className = '' }: { value: number; className?: string }) {
  const tone = value > 0 ? 'text-mint-600' : value < 0 ? 'text-coral-500' : 'text-slate-500';
  const arrow = value > 0 ? '↑' : value < 0 ? '↓' : '→';
  return (
    <span className={`${tone} ${className}`}>
      {value > 0 ? '+' : ''}
      {value.toFixed(2)}% {arrow}
    </span>
  );
}

export function EmptyState({ text, hint }: { text: string; hint?: string }) {
  return (
    <div className="py-10 text-center">
      <p className="text-sm text-slate-500">{text}</p>
      {hint && <p className="text-xs text-slate-400 mt-1">{hint}</p>}
    </div>
  );
}

export function Notice({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'warn';
  children: ReactNode;
}) {
  const cls =
    tone === 'warn'
      ? 'bg-coral-50 border-coral-100 text-coral-700'
      : 'bg-ocean-50 border-ocean-100 text-ocean-700';
  return <div className={`rounded-xl border px-4 py-3 text-xs leading-relaxed ${cls}`}>{children}</div>;
}

/** 图表占位组件：后续替换为 ECharts / TradingView 真实图表 */
export function ChartPlaceholder({
  icon: Icon,
  text,
  iconClass = 'text-brand-300',
  heightClass = 'h-48',
}: {
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  text: string;
  iconClass?: string;
  heightClass?: string;
}) {
  return (
    <div className={`chart-placeholder rounded-xl ${heightClass} flex items-center justify-center`}>
      <div className="text-center">
        <Icon className={`w-16 h-16 mx-auto mb-2 ${iconClass}`} />
        <p className="text-sm text-slate-400">{text}</p>
      </div>
    </div>
  );
}

/** 指标详情页头部（图标 + 名称 + 描述 + 右侧统计） */
export function IndicatorHeader({
  icon: Icon,
  gradientClass,
  title,
  subtitle,
  extra,
}: {
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  gradientClass: string;
  title: string;
  subtitle: string;
  extra?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-4 mb-6">
      <div
        className={`w-12 h-12 rounded-xl bg-gradient-to-br ${gradientClass} flex items-center justify-center text-white`}
      >
        <Icon className="w-6 h-6" />
      </div>
      <div>
        <h3 className="text-2xl font-bold text-slate-800">{title}</h3>
        <p className="text-sm text-slate-500">{subtitle}</p>
      </div>
      {extra && <div className="ml-auto flex gap-3">{extra}</div>}
    </div>
  );
}

export function signalLabel(type: SignalKind): string {
  return type === 'bottom' ? '底部（买入）' : '顶部（卖出）';
}
