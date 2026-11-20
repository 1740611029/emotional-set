import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { SearchIcon, BellIcon } from './icons';
import { GlobalTaskButton } from './TaskButtons';
import { titleByPath } from './Sidebar';
import { useBloggerNotifications } from '../data/BloggerNotificationProvider';
import { usePageMetaContext } from '../data/PageMetaProvider';

const BELL_TITLE: Record<string, string> = {
  granted: '发文桌面提醒已开启，点击发送一条测试通知',
  default: '点击开启【全能的野人】发文桌面提醒',
  denied: '浏览器通知权限被拒绝；点击查看处理办法',
  unsupported: '当前浏览器不支持通知；点击查看说明',
};

export function TopBar() {
  const { pathname } = useLocation();
  const title = titleByPath.get(pathname) ?? 'SignalHub';
  const { permission, enabled, enable, test } = useBloggerNotifications();
  const { meta: pageMeta } = usePageMetaContext();
  // 各页面自行注册数据截止时间（机构持仓交易日、博主记录更新时间等），顶栏不再依赖统一快照
  const asOfText = pageMeta?.asOf?.text ?? '--';
  const asOfWarn = pageMeta?.asOf?.warn ?? false;

  // 铃铛点击反馈：权限被拒、浏览器不支持、授权结果、测试通知都给出可见提示，避免"点了没反应"
  const [bellHint, setBellHint] = useState<string | null>(null);
  const hintTimer = useRef<number | null>(null);
  useEffect(() => () => {
    if (hintTimer.current !== null) window.clearTimeout(hintTimer.current);
  }, []);

  const showBellHint = (text: string) => {
    setBellHint(text);
    if (hintTimer.current !== null) window.clearTimeout(hintTimer.current);
    hintTimer.current = window.setTimeout(() => setBellHint(null), 4500);
  };

  const onBellClick = async () => {
    if (permission === 'unsupported') {
      showBellHint('当前浏览器不支持桌面通知；新发文提醒仍由采集脚本发出系统通知。');
      return;
    }
    if (permission === 'denied') {
      showBellHint('浏览器通知权限已被拒绝。请在地址栏左侧的站点设置里把「通知」改为允许，然后刷新页面。');
      return;
    }
    if (permission === 'default') {
      const result = await enable();
      showBellHint(result === 'granted'
        ? '桌面提醒已开启，再点一次铃铛可发送测试通知。'
        : '未获得通知权限；新发文提醒仍由采集脚本发出系统通知。');
      return;
    }
    test();
    showBellHint('已发送测试通知。若未弹出，请检查 Windows 通知与专注助手设置。');
  };

  const bellIconClass = enabled
    ? 'text-brand-600'
    : permission === 'denied' ? 'text-coral-500' : 'text-slate-500';

  const PageIcon = pageMeta?.icon;
  const pageRefresh = pageMeta?.refresh;
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
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 bg-white/60 rounded-lg px-3 py-1.5 border border-slate-200">
          <SearchIcon className="w-4 h-4 text-slate-400" />
          <input
            type="text"
            placeholder="搜索指标..."
            className="bg-transparent text-sm outline-none w-32 text-slate-600 placeholder-slate-400"
          />
        </div>
        {pageRefresh && (
          <button
            type="button"
            onClick={() => void pageRefresh.onRefresh()}
            disabled={pageRefresh.busy}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-brand-50 text-brand-700 hover:bg-brand-100 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pageRefresh.busy ? pageRefresh.busyLabel : pageRefresh.label}
          </button>
        )}
        <GlobalTaskButton />
        <div className="relative">
          <button
            type="button"
            onClick={() => void onBellClick()}
            title={BELL_TITLE[permission]}
            aria-label={BELL_TITLE[permission]}
            className={`p-2 rounded-lg transition-colors ${enabled ? 'bg-brand-50 hover:bg-brand-100' : 'hover:bg-slate-100'}`}
          >
            <BellIcon className={`w-5 h-5 ${bellIconClass}`} />
          </button>
          {bellHint && (
            <div className="absolute right-0 top-full z-20 mt-2 w-64 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs leading-relaxed text-slate-600 shadow-md">
              {bellHint}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
