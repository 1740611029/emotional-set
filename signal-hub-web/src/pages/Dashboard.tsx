import { useEffect, useState } from 'react';
import { useMarketData } from '../data/MarketDataProvider';
import { EmptyState, LayerDots, Notice, SignalBadge } from '../components/ui';
import { SignalTable } from '../components/SignalTable';
import { IndexSignalChartCard } from '../components/IndexSignalChartCard';
import { AccuracyMatrixPanel } from '../components/AccuracyMatrixPanel';
import { orderIndicesForCharts } from '../lib/dashboardChartLayout';
import {
  DEFAULT_INDEX_CHART_RANGE_KEY,
  INDEX_CHART_RANGES,
  type IndexChartRangeKey,
} from '../lib/indexSignalCharts';

function Loading() {
  return (
    <div className="p-8">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="glass-card rounded-2xl p-6 h-32 animate-pulse" />
        ))}
      </div>
      <div className="glass-card rounded-2xl p-6 h-64 animate-pulse" />
    </div>
  );
}

function Unavailable({ message }: { message: string | null }) {
  const { refresh } = useMarketData();
  return (
    <div className="p-8">
      <div className="glass-card rounded-2xl p-12 text-center">
        <p className="text-slate-600 font-medium">数据暂不可用</p>
        <p className="text-sm text-slate-400 mt-2">{message ?? '请确认数据服务已启动'}</p>
        <button
          type="button"
          onClick={refresh}
          className="mt-5 px-4 py-2 rounded-lg bg-brand-50 text-brand-700 text-sm font-medium hover:bg-brand-100"
        >
          重试
        </button>
      </div>
    </div>
  );
}

export function Dashboard() {
  const { snapshot, error, isLoading } = useMarketData();
  const [chartRange, setChartRange] = useState<IndexChartRangeKey>(DEFAULT_INDEX_CHART_RANGE_KEY);
  const [showMatrix, setShowMatrix] = useState(false);

  useEffect(() => {
    if (!showMatrix) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setShowMatrix(false);
    };
    window.addEventListener('keydown', onKey);
    // 弹窗打开时锁定背景滚动，避免页面出现多余滚动条
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [showMatrix]);

  if (isLoading && !snapshot) return <Loading />;
  if (!snapshot) return <Unavailable message={error} />;
  if (snapshot.data_state === 'preparing') {
    return (
      <div className="p-8">
        <div className="glass-card rounded-2xl p-12 text-center">
          <p className="text-slate-700 font-medium">首次运行，正在准备行情数据</p>
          <p className="text-sm text-slate-400 mt-2">
            {snapshot.progress?.detail || '正在拉取指数与板块日线，请稍候…'}
          </p>
        </div>
      </div>
    );
  }

  const asOf = snapshot.as_of;
  const todayConfirmed = snapshot.recent_signals.filter((row) => row.date === asOf).length;
  const pending = snapshot.candidates.length;
  const busiest = [...snapshot.indices].sort((a, b) => b.signals.length - a.signals.length)[0];
  // 图表主区按用户指定的四行分组顺序展示，仅影响渲染排列
  const chartIndices = orderIndicesForCharts(snapshot.indices);

  return (
    <div className="p-8">
      {snapshot.stale && (
        <div className="mb-6">
          <Notice tone="warn">
            数据源暂不可用，当前展示 {snapshot.stale_since ?? '上一次成功获取'} 的数据。
            {snapshot.stale_reason ? `（${snapshot.stale_reason}）` : ''}
          </Notice>
        </div>
      )}

      {/* Summary Cards：单一「已判定胜率」已由「多持有期准确率详情」弹窗承载 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="glass-card rounded-2xl p-6 fade-in">
          <div className="flex items-center justify-between mb-4">
            <span className="text-sm font-medium text-slate-500">当日确认信号</span>
            <span className="badge-confirm text-xs px-2.5 py-1 rounded-full font-medium">
              {asOf ?? '--'}
            </span>
          </div>
          <div className="flex items-end gap-2">
            <span className="text-3xl font-bold metric-value">{todayConfirmed}</span>
            <span className="text-sm text-slate-400 mb-1">条</span>
          </div>
          <p className="text-xs text-slate-400 mt-2">三层确认通过后计入</p>
        </div>

        <div className="glass-card rounded-2xl p-6 fade-in" style={{ animationDelay: '0.05s' }}>
          <div className="flex items-center justify-between mb-4">
            <span className="text-sm font-medium text-slate-500">待确认候选</span>
            <span className="badge-pending text-xs px-2.5 py-1 rounded-full font-medium">观察</span>
          </div>
          <div className="flex items-end gap-2">
            <span className="text-3xl font-bold text-coral-500">{pending}</span>
            <span className="text-sm text-slate-400 mb-1">个</span>
          </div>
          <p className="text-xs text-slate-400 mt-2">1-3 个交易日内等待确认</p>
        </div>

        <div className="glass-card rounded-2xl p-6 fade-in" style={{ animationDelay: '0.1s' }}>
          <div className="flex items-center justify-between mb-4">
            <span className="text-sm font-medium text-slate-500">信号最活跃指数</span>
            <span className="bg-brand-100 text-brand-700 text-xs px-2.5 py-1 rounded-full font-medium">
              {snapshot.indices.length} 个
            </span>
          </div>
          <div className="flex items-end gap-2">
            <span className="text-3xl font-bold text-slate-700">{busiest?.name ?? '--'}</span>
          </div>
          <p className="text-xs text-slate-400 mt-2">历史共 {busiest?.signals.length ?? 0} 条信号</p>
        </div>
      </div>

      {/* 多持有期准确率矩阵：旧矩阵基于原 swing 信号，新底部信号不再产出该块，缺失时不显示入口 */}
      {snapshot.precision && (
        <div className="mb-8 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => setShowMatrix(true)}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-brand-500 text-white text-sm font-medium shadow-sm hover:bg-brand-600 transition-colors"
          >
            <span aria-hidden>📊</span> 多持有期准确率详情
          </button>
          <span className="text-xs text-slate-400">
            按 5 / 10 / 20 / 30 日分别统计，含方向正确率、有效幅度率、Wilson 区间与浮亏约束；点击展开完整矩阵
          </span>
        </div>
      )}

      {/* 指数走势与信号 */}
      <section className="mb-8 fade-in" style={{ animationDelay: '0.2s' }}>
        <div className="glass-card rounded-2xl p-5 mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-bold text-slate-800">7 大宽基指数走势与信号（图示范围）</h3>
            <p className="text-xs text-slate-400 mt-1">
              下方 7 张图卡是展示范围；点击上方「多持有期准确率详情」查看完整矩阵，其统计覆盖同为这 7 个标的，二者一致但不等于样本条数
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs text-slate-500">
            时间范围
            <select
              aria-label="指数图表时间范围"
              value={chartRange}
              onChange={(event) => setChartRange(event.target.value as IndexChartRangeKey)}
              className="px-3 py-1.5 text-xs rounded-lg bg-slate-50 text-slate-600 border border-slate-100 focus:outline-none focus:ring-2 focus:ring-brand-200"
            >
              {INDEX_CHART_RANGES.map((option) => (
                <option key={option.key} value={option.key}>{option.label}</option>
              ))}
            </select>
          </label>
        </div>

        {snapshot.indices.length === 0 ? (
          <div className="glass-card rounded-2xl p-6">
            <EmptyState text="暂无指数数据" hint="请确认数据服务已完成首次数据拉取" />
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {chartIndices.map((item, index) => (
              <IndexSignalChartCard
                key={item.code}
                item={item}
                range={chartRange}
                endDate={asOf ?? undefined}
                anchors={snapshot.precision?.pivot_anchors?.[item.code] ?? []}
                className={index === 0 ? 'md:col-span-2' : ''}
              />
            ))}
          </div>
        )}
      </section>

      {/* 最新信号 */}
      <div className="glass-card rounded-2xl p-6 mb-8 fade-in" style={{ animationDelay: '0.22s' }}>
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-bold text-slate-800">最新信号状态</h3>
          <div className="flex gap-3 text-xs text-slate-500">
            <span className="flex items-center gap-1">
              <span className="signal-dot confirm"></span>大波段底部信号
            </span>
            <span className="text-slate-400">顶部信号与研究阶段，暂未输出</span>
          </div>
        </div>
        <SignalTable
          rows={snapshot.recent_signals}
          asOf={asOf}
          provisional={snapshot.provisional}
          limit={8}
        />
      </div>

      {/* 待确认候选 */}
      <div className="glass-card rounded-2xl p-6 mb-8 fade-in" style={{ animationDelay: '0.25s' }}>
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-bold text-slate-800">待确认候选</h3>
          <span className="text-xs text-slate-400">价格 → 动量 → 量能，三层全部通过才转为已确认</span>
        </div>
        {snapshot.candidates.length === 0 ? (
          <EmptyState text="当前没有处于确认窗口内的候选信号" hint="候选触发后需 1-3 个交易日观察" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-100">
                  {['触发日期', '指数', '方向', '触发价', '确认进度', '已等待'].map((header) => (
                    <th
                      key={header}
                      className="text-left text-xs font-semibold text-slate-400 uppercase tracking-wider py-3 px-4"
                    >
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="text-sm">
                {snapshot.candidates.map((row) => (
                  <tr
                    key={`${row.code}-${row.date}`}
                    className="border-b border-slate-50 last:border-0 hover:bg-slate-50/50"
                  >
                    <td className="py-3 px-4 text-slate-600 tabular-nums">{row.date}</td>
                    <td className="py-3 px-4 font-medium text-slate-800">{row.name ?? row.code}</td>
                    <td className="py-3 px-4">
                      <SignalBadge type={row.type} size="sm" />
                    </td>
                    <td className="py-3 px-4 text-slate-600 tabular-nums">
                      {row.price.toLocaleString('zh-CN', {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </td>
                    <td className="py-3 px-4">
                      <LayerDots layers={row.layers} passed={row.passed} />
                    </td>
                    <td className="py-3 px-4 text-slate-500">{row.trading_days_since} 个交易日</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 多持有期准确率矩阵弹窗：首页零常驻空间，按需展开 */}
      {showMatrix && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
          onClick={() => setShowMatrix(false)}
        >
          <div
            className="relative flex flex-col w-full max-w-5xl h-[90vh] rounded-2xl bg-white shadow-xl overflow-hidden"
            onClick={(event) => event.stopPropagation()}
          >
            <AccuracyMatrixPanel snapshot={snapshot} bare onClose={() => setShowMatrix(false)} />
          </div>
        </div>
      )}
    </div>
  );
}
