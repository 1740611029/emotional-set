import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { SearchIcon, BellIcon } from './icons';
import { GlobalTaskButton } from './TaskButtons';
import { titleByPath } from './Sidebar';
import { useMarketData } from '../data/MarketDataProvider';
import { useBloggerNotifications } from '../data/BloggerNotificationProvider';
import { useTaskControl } from '../data/TaskControlProvider';

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

const BELL_TITLE: Record<string, string> = {
  granted: '发文桌面提醒已开启，点击发送一条测试通知',
  default: '点击开启【全能的野人】发文桌面提醒',
  denied: '浏览器通知权限被拒绝；点击查看处理办法',
  unsupported: '当前浏览器不支持通知；点击查看说明',
};

export function TopBar() {
  const { pathname } = useLocation();
  const title = titleByPath.get(pathname) ?? '指标总览';
  const { snapshot, error, isLoading, lastSuccessAt, refresh } = useMarketData();
  const { permission, enabled, enable, test } = useBloggerNotifications();
  const { enabled: taskEnabled } = useTaskControl();
  const snapshotPollOn = taskEnabled.snapshot_poll;

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
            className="bg-transparent text-sm outline-none w-32 text-slate-600 placeholder-slate-400"
          />
        </div>
        <div className="text-right leading-tight">
          <div className="text-xs text-slate-500 tabular-nums">
            {error ? `更新失败：${error}` : lastSuccessAt ? `更新于 ${lastSuccessAt}` : '加载中…'}
          </div>
          <div className={`text-[11px] ${snapshotPollOn ? 'text-slate-400' : 'text-coral-500'}`}>
            {snapshotPollOn ? '每 30 秒自动刷新' : '自动刷新已停止'}
          </div>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={isLoading}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-brand-50 text-brand-700 hover:bg-brand-100 transition-colors disabled:opacity-50"
        >
          {isLoading ? '刷新中' : '立即刷新'}
        </button>
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
