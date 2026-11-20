import { useLocation } from 'react-router-dom';
import { titleByPath } from './Sidebar';
import { usePageMetaContext } from '../data/PageMetaProvider';

export function TopBar() {
  const { pathname } = useLocation();
  const title = titleByPath.get(pathname) ?? 'SignalHub';
  const { meta: pageMeta } = usePageMetaContext();
  // 各页面自行注册数据截止时间（机构持仓交易日、博主记录更新时间等），顶栏不再依赖统一快照
  const asOfText = pageMeta?.asOf?.text ?? '--';
  const asOfWarn = pageMeta?.asOf?.warn ?? false;

  const PageIcon = pageMeta?.icon;
  const hasSecondRow = Boolean(pageMeta?.subtitle || pageMeta?.stats?.length);

  return (
    <header className="sticky top-0 z-10 glass-card border-b border-slate-100 px-8 py-4 flex items-center justify-between gap-4">
      <div className="flex items-center gap-3 min-w-0">
        {PageIcon && pageMeta?.gradientClass && (
          <div
            className={`w-9 h-9 rounded-lg bg-gradient-to-br ${pageMeta.gradientClass} flex items-center justify-center text-white shrink-0`}
          >
            <PageIcon className="w-5 h-5" />
          </div>
        )}
        <div className="min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-xl font-bold text-slate-800">{title}</h2>
            <span className="text-sm text-slate-300">|</span>
            <span
              className={`text-sm tabular-nums ${asOfWarn ? 'text-coral-600 font-medium' : 'text-slate-500'}`}
            >
              {asOfText}
            </span>
          </div>
          {hasSecondRow && (
            <div className="flex items-baseline gap-x-3 gap-y-0.5 mt-0.5 flex-wrap">
              {pageMeta?.subtitle && (
                <p className="text-xs text-slate-400 truncate max-w-full">{pageMeta.subtitle}</p>
              )}
              {pageMeta?.stats?.map((stat) => (
                <span key={stat.label} className="text-xs text-slate-400 tabular-nums shrink-0">
                  {stat.label}
                  <span className="text-slate-500 ml-1">{stat.value}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
