import { useLocation } from 'react-router-dom';
import { SearchIcon, BellIcon } from './icons';
import { titleByPath } from './Sidebar';
import { useMarketData } from '../data/MarketDataProvider';

function ModeBadge({ mode }: { mode: 'live' | 'close' | null }) {
  if (mode === 'live') {
    return (
      <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-ocean-50 text-ocean-700">
        <span className="signal-dot confirm"></span>盘中实时
      </span>
    );
  }
  if (mode === 'close') {
    return (
      <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-slate-100 text-slate-600">
        <span className="signal-dot neutral"></span>收盘数据
      </span>
    );
  }
  return (
    <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-slate-100 text-slate-500">
      数据准备中
    </span>
  );
}

export function TopBar() {
  const { pathname } = useLocation();
  const title = titleByPath.get(pathname) ?? '指标总览';
  const { snapshot, error, isLoading, lastSuccessAt, refresh } = useMarketData();

  return (
    <header className="sticky top-0 z-10 glass-card border-b border-slate-100 px-8 py-4 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <h2 className="text-xl font-bold text-slate-800">{title}</h2>
        <span className="text-sm text-slate-300">|</span>
        <span className="text-sm text-slate-500 tabular-nums">{snapshot?.as_of ?? '--'}</span>
        <ModeBadge mode={snapshot?.mode ?? null} />
        {snapshot?.stale && (
          <span className="text-xs px-2 py-1 rounded-full bg-coral-50 text-coral-600 font-medium">
            数据陈旧
          </span>
        )}
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 bg-white/60 rounded-lg px-3 py-1.5 border border-slate-200">
          <SearchIcon className="w-4 h-4 text-slate-400" />
          <input
            type="text"
            placeholder="搜索指标..."
            className="bg-transparent text-sm outline-none w-40 text-slate-600 placeholder-slate-400"
          />
        </div>
        <div className="text-right leading-tight">
          <div className="text-xs text-slate-500 tabular-nums">
            {error ? `更新失败：${error}` : lastSuccessAt ? `更新于 ${lastSuccessAt}` : '加载中…'}
          </div>
          <div className="text-[11px] text-slate-400">每 30 秒自动刷新</div>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={isLoading}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-brand-50 text-brand-700 hover:bg-brand-100 transition-colors disabled:opacity-50"
        >
          {isLoading ? '刷新中' : '立即刷新'}
        </button>
        <button className="p-2 rounded-lg hover:bg-slate-100 transition-colors">
          <BellIcon className="w-5 h-5 text-slate-500" />
        </button>
      </div>
    </header>
  );
}
