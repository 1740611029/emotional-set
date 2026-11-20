import { useMemo } from 'react';
import { EmptyState, Notice } from '../components/ui';
import { VolumeIcon } from '../components/icons';
import { EtfAmountLinesChart, TotalVolumeIndexChart } from '../components/EtfVolumeCharts';
import { EtfShareFlowChart } from '../components/EtfShareFlowChart';
import { useEtfVolumeSeries } from '../hooks/useEtfVolumeSeries';
import { useEtfShareFlow } from '../hooks/useEtfShareFlow';
import { usePageMeta } from '../data/PageMetaProvider';
import { formatTimestamp } from '../lib/formatTimestamp';
import { sliceEtfSeriesToView } from '../lib/etfVolumeSeries';
import { buildShareFlowView, formatSignedYi, sumRecent } from '../lib/etfShareFlow';

export function EtfVolume() {
  const { data, error, isLoading, refresh } = useEtfVolumeSeries();
  const {
    data: flow, error: flowError, isLoading: flowLoading, refresh: refreshFlow,
  } = useEtfShareFlow();
  // 时间范围统一交给各图自带的 dataZoom 横向滑块在前端裁切，页面不再保留下拉筛选（需求 2026-11-20）
  const view = useMemo(
    () => (data && data.data_state !== 'preparing' ? sliceEtfSeriesToView(data, 'all') : null),
    [data],
  );
  const flowView = useMemo(
    () => (flow && flow.data_state !== 'preparing' ? buildShareFlowView(flow) : null),
    [flow],
  );

  const hasData = Boolean(data && data.data_state !== 'preparing' && data.dates.length > 0);
  const hasFlow = Boolean(flowView && flowView.dates.length > 0);

  // 页面标题/描述/数据截止/手动刷新上移到 TopBar（需求 2026-11-17），内容区不再渲染 IndicatorHeader
  usePageMeta({
    icon: VolumeIcon,
    gradientClass: 'from-brand-400 to-ocean-400',
    subtitle: '核心 6 只宽基 ETF 单日成交额、合计规模与上证指数走势的同坐标系对照',
    asOf: { text: data?.as_of ?? '—' },
    stats: [
      ...(data?.generated_at ? [{ label: '成交额更新', value: formatTimestamp(data.generated_at) ?? '—' }] : []),
      ...(flow?.generated_at ? [{ label: '净流入更新', value: formatTimestamp(flow.generated_at) ?? '—' }] : []),
    ],
    refresh: {
      label: '手动刷新', busyLabel: '刷新中', busy: isLoading || flowLoading,
      onRefresh: () => { refresh(); refreshFlow(); },
    },
  });

  return (
    <div className="p-6">

      {error && (
        <div className="mb-5">
          <Notice tone="warn">成交额刷新失败：{error}。{data ? '当前保留最近一次成功数据。' : '暂无可展示数据，请重试。'}</Notice>
        </div>
      )}
      {flowError && (
        <div className="mb-5">
          <Notice tone="warn">净流入刷新失败：{flowError}。{flow ? '当前保留最近一次成功数据。' : '暂无可展示数据，请重试。'}</Notice>
        </div>
      )}
      {data?.stale && data.data_state !== 'preparing' && (
        <div className="mb-5">
          <Notice tone="warn">成交额数据陈旧：{data.stale_reason ?? 'ETF 日线尚未更新到最近交易日'}，当前仍展示已落盘数据。</Notice>
        </div>
      )}
      {flow?.stale && flow.data_state !== 'preparing' && (
        <div className="mb-5">
          <Notice tone="warn">净流入数据陈旧：{flow.stale_reason ?? '份额/净值尚未更新到最近交易日'}，当前仍展示已落盘数据。</Notice>
        </div>
      )}

      {!data && isLoading && (
        <div className="glass-card rounded-2xl p-12 text-center text-sm text-slate-500">ETF 成交额序列加载中…</div>
      )}
      {!data && !isLoading && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState text="ETF 成交额数据暂不可用" hint="请检查数据服务与落盘产物；页面不会以 0 或空数组绘制占位折线" />
        </div>
      )}
      {data?.data_state === 'preparing' && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState text="ETF 成交额数据准备中" hint={data.stale_reason ?? '日线落盘完成后将在此展示，页面不会以占位数值替代'} />
        </div>
      )}

      {/* 第一张图：宽基 ETF 申购净流入（红绿柱 + 上证指数 + 横向时间滑块），独立于成交额两图 */}
      {flow?.data_state === 'preparing' && (
        <div className="glass-card rounded-2xl p-6 mb-8">
          <EmptyState text="宽基 ETF 申购净流入数据准备中" hint={flow.stale_reason ?? '份额与单位净值落盘完成后将在此展示，页面不会以占位数值替代'} />
        </div>
      )}
      {hasFlow && flow && flowView && (
        <div className="glass-card rounded-2xl p-6 mb-8">
          <div className="flex flex-wrap items-end justify-between gap-4 mb-4">
            <div>
              <h3 className="text-lg font-bold text-slate-800 mb-1">宽基 ETF 申购净流入</h3>
              <p className="text-xs text-slate-400">
                左轴净流入（亿元，红=净流入/绿=净流出），右轴上证指数点位；拖动图表下方横向滑块筛选时间范围
              </p>
            </div>
            <div className="flex flex-wrap justify-end gap-x-6 gap-y-2">
              <div className="text-right">
                <div className="text-xs text-slate-400">昨日净流入</div>
                <div className={`text-lg font-bold ${(flow.stats.last_day_yi ?? 0) >= 0 ? 'text-coral-500' : 'text-brand-500'}`}>
                  {flow.stats.last_day_yi === null ? '—' : formatSignedYi(flow.stats.last_day_yi)} 亿
                </div>
              </div>
              {([10, 20, 30] as const).map((days) => {
                const value = sumRecent(flowView.inflowYi, days);
                return (
                  <div className="text-right" key={days}>
                    <div className="text-xs text-slate-400">近{days}日净流入</div>
                    <div className={`text-lg font-bold ${value >= 0 ? 'text-coral-500' : 'text-brand-500'}`}>
                      {formatSignedYi(value)} 亿
                    </div>
                  </div>
                );
              })}
              <div className="text-right">
                <div className="text-xs text-slate-400">近{flow.stats.last_60d_days}日净流入</div>
                <div className={`text-lg font-bold ${flow.stats.last_60d_yi >= 0 ? 'text-coral-500' : 'text-brand-500'}`}>
                  {formatSignedYi(flow.stats.last_60d_yi)} 亿
                </div>
              </div>
            </div>
          </div>
          <EtfShareFlowChart view={flowView} loading={flowLoading && !flow} />
          <p className="text-xs text-slate-400 mt-2">
            口径：Σ(份额变化×单位净值)，覆盖 {flow.coverage.etf_count} 只宽基 ETF（沪深300/中证A500/中证500/中证1000/上证50/科创50/创业板等）；
            唯一统计区间 {flow.coverage.start} ~ {flow.coverage.end}（{flow.coverage.trading_days} 个交易日）。
          </p>
        </div>
      )}

      {data && hasData && view && view.dates.length > 0 && (
        <>
          <div className="glass-card rounded-2xl p-6 mb-8">
            <h3 className="text-lg font-bold text-slate-800 mb-1">合计成交额与上证指数</h3>
            <p className="text-xs text-slate-400 mb-4">左轴合计成交额（亿元），右轴上证指数点位；拖动图表下方横向滑块筛选时间范围，标记为全池爆量日（L3），缺失指数日保持断点不插值</p>
            <TotalVolumeIndexChart view={view} loading={isLoading} />
          </div>
          <div className="glass-card rounded-2xl p-6">
            <h3 className="text-lg font-bold text-slate-800 mb-1">6 只 ETF 单只成交额</h3>
            <p className="text-xs text-slate-400 mb-4">共用同一亿元纵轴，体量按真实金额呈现；拖动图表下方横向滑块筛选时间范围，点击图例可隐藏单只，需要单看某只时隐藏其余</p>
            <EtfAmountLinesChart view={view} loading={isLoading} />
          </div>
        </>
      )}
    </div>
  );
}
