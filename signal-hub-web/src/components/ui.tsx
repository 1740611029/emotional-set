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

/** 涨跌幅文本：沿用项目色彩系统（珊瑚橙上涨 / 薄荷绿下跌），保留平盘 slate */
export function ChangeText({ value, className = '' }: { value: number; className?: string }) {
  const tone = value > 0 ? 'text-coral-500' : value < 0 ? 'text-mint-600' : 'text-slate-500';
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

/** 折叠区方向指示箭头：收起朝右、展开旋转 90° 朝下（需求 2026-11-16：原字符箭头过小不明显） */
export function CollapseChevron({ open, className = '' }: { open: boolean; className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden="true"
      className={`w-4 h-4 shrink-0 text-slate-400 transition-transform duration-200 ${open ? 'rotate-90' : ''} ${className}`}
    >
      <path
        fillRule="evenodd"
        d="M6.28 5.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L9.94 10 6.28 6.28a.75.75 0 0 1 0-1.06Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

/** 指标详情页头部（图标 + 名称 + 描述 + 右侧统计）；紧凑单行版，压缩首屏占位（需求 2026-11-16） */
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
    <div className="flex items-center gap-3 mb-3">
      <div
        className={`w-9 h-9 rounded-lg bg-gradient-to-br ${gradientClass} flex items-center justify-center text-white shrink-0`}
      >
        <Icon className="w-5 h-5" />
      </div>
      <div className="min-w-0 flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
        <h3 className="text-lg font-bold text-slate-800">{title}</h3>
        <p className="text-xs text-slate-400">{subtitle}</p>
      </div>
      {extra && <div className="ml-auto flex gap-3 shrink-0">{extra}</div>}
    </div>
  );
}

export function signalLabel(type: SignalKind): string {
  return type === 'bottom' ? '底部（买入）' : '顶部（卖出）';
}
