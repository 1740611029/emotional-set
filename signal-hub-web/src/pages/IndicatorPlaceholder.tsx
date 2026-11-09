import type { ComponentType, SVGProps } from 'react';
import { IndicatorPage } from './IndicatorDetail';
import {
  MomentumIcon,
  BollingerIcon,
  VolumeIcon,
  DrawdownIcon,
  CompositeIcon,
} from '../components/icons';

/**
 * 其余指标页：与均线交叉共用同一模板，数据来自统一数据层。
 * 单指标明细序列尚未纳入数据契约，页面上会明确标注。
 */
const meta: Record<
  string,
  { title: string; subtitle: string; gradientClass: string; icon: ComponentType<SVGProps<SVGSVGElement>> }
> = {
  momentum: {
    title: '动量指标',
    subtitle: 'Momentum · 基于 RSI/MACD 等动量指标的反转与趋势确认',
    gradientClass: 'from-ocean-400 to-brand-400',
    icon: MomentumIcon,
  },
  bollinger: {
    title: '布林带',
    subtitle: 'Bollinger Bands · 基于价格波动率通道的突破与回归信号',
    gradientClass: 'from-coral-400 to-brand-400',
    icon: BollingerIcon,
  },
  volume: {
    title: '量能指标',
    subtitle: 'Volume · 基于成交量变化的量价配合确认信号',
    gradientClass: 'from-mint-400 to-ocean-400',
    icon: VolumeIcon,
  },
  drawdown: {
    title: '回撤/反弹',
    subtitle: 'Drawdown/Rebound · 基于最大回撤与反弹力度的反转信号',
    gradientClass: 'from-brand-400 to-coral-400',
    icon: DrawdownIcon,
  },
  composite: {
    title: '综合评分',
    subtitle: 'Composite Score · 多因子加权综合评分与分层确认',
    gradientClass: 'from-ocean-400 to-mint-400',
    icon: CompositeIcon,
  },
};

export function IndicatorPlaceholder({ indicatorKey }: { indicatorKey: keyof typeof meta }) {
  return <IndicatorPage config={meta[indicatorKey]} />;
}
