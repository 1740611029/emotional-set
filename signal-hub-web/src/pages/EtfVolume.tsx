import { useMemo, useState } from 'react';
import { EmptyState, IndicatorHeader, Notice } from '../components/ui';
import { VolumeIcon } from '../components/icons';
import { EtfAmountLinesChart, TotalVolumeIndexChart } from '../components/EtfVolumeCharts';
import { useEtfVolumeSeries } from '../hooks/useEtfVolumeSeries';
import { sliceEtfSeriesToView, ETF_VOLUME_THRESHOLD_YI } from '../lib/etfVolumeSeries';
import {
  DEFAULT_INDEX_CHART_RANGE_KEY,
  INDEX_CHART_RANGES,
  indexChartRangeOption,
  type IndexChartRangeKey,
} from '../lib/indexSignalCharts';

export function EtfVolume() {
  const { data, error, isLoading, refresh } = useEtfVolumeSeries();
  const [range, setRange] = useState<IndexChartRangeKey>(DEFAULT_INDEX_CHART_RANGE_KEY);

  const view = useMemo(
    () => (data && data.data_state !== 'preparing' ? sliceEtfSeriesToView(data, range) : null),
    [data, range],
  );

  const hasData = Boolean(data && data.data_state !== 'preparing' && data.dates.length > 0);
  const rangeLabel = indexChartRangeOption(range).label;

  return (
    <div className="p-8">
      <IndicatorHeader
        icon={VolumeIcon}
        gradientClass="from-brand-400 to-ocean-400"
        title="ETF 放量"
        subtitle="核心 6 只宽基 ETF 单日成交额、合计规模与上证指数走势的同坐标系对照"
        extra={(
          <div className="text-right text-xs text-slate-400">
            <div>数据交易日：<span className="text-slate-600">{data?.as_of ?? '—'}</span></div>
            <div className="mt-1">最近更新：<span className="text-slate-600">{data?.generated_at?.replace('T', ' ') ?? '—'}</span></div>
            <button
              type="button"
              onClick={refresh}
              className="mt-2 px-3 py-1 rounded-lg bg-brand-50 text-brand-700 font-medium hover:bg-brand-100 transition-colors"
            >
              手动刷新
            </button>
          </div>
        )}
      />

      <div className="mb-5">
        <Notice>
          合计成交额、单只成交额与爆量日标记均为已发生数据的事后描述，不构成预测或买卖信号。
          全池爆量（L3）口径：核心池 6 只当日单日成交额均 ≥ {ETF_VOLUME_THRESHOLD_YI} 亿元人民币。
          序列起点 {data?.coverage.start ?? '—'}（共 {data?.coverage.trading_days ?? '—'} 个齐备交易日），
          由核心池中上市最晚的标的决定——起点之前尚无齐备日线，故不画线、不补零、不以部分标的之和冒充合计。
        </Notice>
      </div>

      {error && (
        <div className="mb-5">
          <Notice tone="warn">刷新失败：{error}。{data ? '当前保留最近一次成功数据。' : '暂无可展示数据，请重试。'}</Notice>
        </div>
      )}
      {data?.stale && data.data_state !== 'preparing' && (
        <div className="mb-5">
          <Notice tone="warn">数据陈旧：{data.stale_reason ?? 'ETF 日线尚未更新到最近交易日'}，当前仍展示已落盘数据。</Notice>
        </div>
      )}

      {/* 页面级时间范围下拉：两图共用，切换只裁切片段，不重新请求、不改变数值 */}
      {hasData && (
        <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            时间范围
            <select
              aria-label="时间范围"
              value={range}
              onChange={(event) => setRange(event.target.value as IndexChartRangeKey)}
              className="px-3 py-1.5 text-sm rounded-lg bg-slate-50 text-slate-600 border border-slate-100"
            >
              {INDEX_CHART_RANGES.map((option) => (
                <option key={option.key} value={option.key}>{option.label}</option>
              ))}
            </select>
          </label>
          <span className="text-xs text-slate-400">
            当前 {rangeLabel}：{view?.dates.length ?? 0} 个交易日，两图同步裁切
          </span>
        </div>
      )}

      {!data && isLoading && (
        <div className="glass-card rounded-2xl p-12 text-center text-sm text-slate-500">ETF 成交额序列加载中…</div>
      )}
      {!data && !isLoading && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState text="ETF 成交额数据暂不可用" hint="请检查数据服务后点击“手动刷新”重试；页面不会以 0 或空数组绘制占位折线" />
        </div>
      )}
      {data?.data_state === 'preparing' && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState text="ETF 成交额数据准备中" hint={data.stale_reason ?? '日线落盘完成后将在此展示，页面不会以占位数值替代'} />
        </div>
      )}

      {data && data.data_state !== 'preparing' && hasData && view && view.dates.length === 0 && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState text={`「${rangeLabel}」区间无齐备数据`} hint="该时间范围末端早于序列起点，请切换更长的时间范围" />
        </div>
      )}

      {data && hasData && view && view.dates.length > 0 && (
        <>
          <div className="glass-card rounded-2xl p-6 mb-8">
            <h3 className="text-lg font-bold text-slate-800 mb-1">合计成交额与上证指数</h3>
            <p className="text-xs text-slate-400 mb-4">左轴合计成交额（亿元），右轴上证指数点位；标记为全池爆量日（L3），缺失指数日保持断点不插值</p>
            <TotalVolumeIndexChart view={view} loading={isLoading} />
          </div>
          <div className="glass-card rounded-2xl p-6">
            <h3 className="text-lg font-bold text-slate-800 mb-1">6 只 ETF 单只成交额</h3>
            <p className="text-xs text-slate-400 mb-4">共用同一亿元纵轴，体量按真实金额呈现；点击图例可隐藏单只，需要单看某只时隐藏其余</p>
            <EtfAmountLinesChart view={view} loading={isLoading} />
          </div>
        </>
      )}
    </div>
  );
}
