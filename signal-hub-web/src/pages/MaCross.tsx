import { IndicatorPage } from './IndicatorDetail';
import { MaCrossIcon } from '../components/icons';

/** 均线交叉详情页：展示真实确认型信号与三层确认状态 */
export function MaCross() {
  return (
    <IndicatorPage
      config={{
        title: '均线交叉',
        subtitle: 'MA Crossover · 基于短期与长期均线交叉点的趋势确认信号',
        gradientClass: 'from-brand-400 to-mint-400',
        icon: MaCrossIcon,
      }}
    />
  );
}
