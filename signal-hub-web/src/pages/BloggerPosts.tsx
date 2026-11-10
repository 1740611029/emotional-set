import { useMemo, useState } from 'react';
import { EmptyState, IndicatorHeader, Notice } from '../components/ui';
import { PostIcon } from '../components/icons';
import { useBloggerPosts } from '../hooks/useBloggerPosts';
import type { BloggerPost } from '../data/types';

type RangeKey = 'all' | 'today' | '3d' | '7d' | '14d' | '30d' | 'custom';

const RANGE_OPTIONS: { key: RangeKey; label: string; days: number }[] = [
  { key: 'all', label: '全部', days: 0 },
  { key: 'today', label: '今天', days: 1 },
  { key: '3d', label: '近 3 天', days: 3 },
  { key: '7d', label: '近 7 天', days: 7 },
  { key: '14d', label: '近半个月', days: 15 },
  { key: '30d', label: '近一个月', days: 30 },
];

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

const pad = (n: number) => String(n).padStart(2, '0');

/** 北京时间的今天（YYYY-MM-DD），不受运行机器时区影响 */
function beijingToday(): string {
  const now = new Date();
  const beijing = new Date(now.getTime() + (now.getTimezoneOffset() + 480) * 60000);
  return `${beijing.getFullYear()}-${pad(beijing.getMonth() + 1)}-${pad(beijing.getDate())}`;
}

/** 日期字符串平移天数（按 UTC 计算，避免夏令时/时区干扰） */
function shiftDate(date: string, days: number): string {
  const [y, m, d] = date.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`;
}

function weekdayOf(date: string): string {
  const [y, m, d] = date.split('-').map(Number);
  return WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
}

/** 正文里的 $股票$ 标记保留原文字符，只做轻量高亮 */
function renderText(text: string) {
  const parts = text.split(/(\$[^$\s]{1,20}\$)/g);
  return parts.map((part, index) => (
    part.startsWith('$') && part.endsWith('$') && part.length > 2
      ? (
        <span key={index} className="mx-0.5 rounded bg-brand-50 px-1 py-0.5 font-medium text-brand-700">
          {part}
        </span>
      )
      : <span key={index}>{part}</span>
  ));
}

export function BloggerPosts() {
  const { data, error, isLoading, refresh } = useBloggerPosts();
  const [range, setRange] = useState<RangeKey>('all');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');

  const today = beijingToday();

  const [from, to] = useMemo<[string, string]>(() => {
    if (range === 'custom') return [customFrom, customTo];
    if (range === 'all') return ['', ''];
    const option = RANGE_OPTIONS.find((item) => item.key === range) ?? RANGE_OPTIONS[0];
    return [shiftDate(today, -(option.days - 1)), today];
  }, [range, customFrom, customTo, today]);

  const posts = data?.posts ?? [];

  const filtered = useMemo(
    () => posts.filter((post) => (!from || post.date >= from) && (!to || post.date <= to)),
    [posts, from, to],
  );

  const grouped = useMemo(() => {
    const map = new Map<string, BloggerPost[]>();
    for (const post of filtered) {
      const list = map.get(post.date);
      if (list) list.push(post);
      else map.set(post.date, [post]);
    }
    return Array.from(map.entries()).sort((a, b) => b[0].localeCompare(a[0]));
  }, [filtered]);

  const rangeLabel = range === 'custom'
    ? `${from || '不限'} ~ ${to || '不限'}`
    : RANGE_OPTIONS.find((item) => item.key === range)?.label ?? '';

  const hasData = posts.length > 0;

  return (
    <div className="p-8">
      <IndicatorHeader
        icon={PostIcon}
        gradientClass="from-brand-400 to-mint-400"
        title="【全能的野人】发文"
        subtitle="同花顺博主「全能的野人」动态自动采集，只收录最近一个月的公开发文"
        extra={(
          <div className="text-right text-xs text-slate-400">
            <div>记录更新：<span className="text-slate-600 tabular-nums">{data?.updated_at ?? '—'}</span></div>
            <div className="mt-1">累计收录：<span className="text-slate-600 tabular-nums">{data?.total ?? 0} 条</span></div>
            <button
              type="button"
              onClick={refresh}
              className="mt-2 rounded-lg bg-brand-50 px-3 py-1 font-medium text-brand-700 transition-colors hover:bg-brand-100"
            >
              手动刷新
            </button>
          </div>
        )}
      />

      <div className="mb-5">
        <Notice>
          数据由本机计划任务抓取：交易时段每 5 分钟一次、其余时段每 30 分钟一次，只记录发布时间、关联个股标签与正文原文，
          超过一个月的内容自动清理。页面仅做展示，不构成任何投资建议。
        </Notice>
      </div>

      {error && (
        <div className="mb-5">
          <Notice tone="warn">
            刷新失败：{error}。{data ? '当前保留最近一次成功数据。' : '暂无可展示数据，请重试。'}
          </Notice>
        </div>
      )}

      <div className="glass-card mb-5 rounded-2xl p-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-2">
            {RANGE_OPTIONS.map((option) => {
              const active = range === option.key;
              return (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setRange(option.key)}
                  className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${
                    active ? 'bg-brand-500 text-white shadow-sm shadow-brand-200' : 'bg-slate-50 text-slate-600 hover:bg-brand-50 hover:text-brand-700'
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
            <button
              type="button"
              onClick={() => setRange('custom')}
              className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${
                range === 'custom' ? 'bg-brand-500 text-white shadow-sm shadow-brand-200' : 'bg-slate-50 text-slate-600 hover:bg-brand-50 hover:text-brand-700'
              }`}
            >
              自定义
            </button>
          </div>

          {range === 'custom' && (
            <div className="flex items-center gap-2 text-sm text-slate-600">
              <input
                type="date"
                aria-label="开始日期"
                value={customFrom}
                max={customTo || undefined}
                onChange={(event) => setCustomFrom(event.target.value)}
                className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-1.5 text-sm text-slate-600"
              />
              <span className="text-slate-300">至</span>
              <input
                type="date"
                aria-label="结束日期"
                value={customTo}
                min={customFrom || undefined}
                onChange={(event) => setCustomTo(event.target.value)}
                className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-1.5 text-sm text-slate-600"
              />
            </div>
          )}

          <span className="text-xs text-slate-400">
            {rangeLabel} ｜ {filtered.length} 条 ／ 共 {posts.length} 条
          </span>
        </div>
      </div>

      {isLoading && !data && (
        <div className="glass-card rounded-2xl p-12 text-center text-sm text-slate-500">博主动态加载中…</div>
      )}

      {!isLoading && !hasData && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState
            text="暂无采集到的发文"
            hint="采集脚本运行并落盘后即可展示；也可点击右上角「手动刷新」重试"
          />
        </div>
      )}

      {hasData && filtered.length === 0 && (
        <div className="glass-card rounded-2xl p-6">
          <EmptyState text="所选时间范围内没有发文" hint="换个时间范围试试，或选择「全部」查看所有记录" />
        </div>
      )}

      <div className="space-y-6">
        {grouped.map(([date, list]) => (
          <section key={date}>
            <div className="mb-3 flex items-center gap-3">
              <span className="text-sm font-semibold text-slate-700 tabular-nums">{date}</span>
              <span className="text-xs text-slate-400">{weekdayOf(date)}</span>
              <span className="rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700">
                {list.length} 条
              </span>
              <span className="h-px flex-1 bg-slate-100" />
            </div>

            <div className="space-y-3">
              {list.map((post) => (
                <article key={post.id} className="glass-card glass-card-hover rounded-2xl p-5 transition-all">
                  <div className="flex items-start gap-4">
                    <div className="w-16 shrink-0 text-right">
                      <div className="text-lg font-bold text-slate-800 tabular-nums">{post.time}</div>
                      <div className="mt-0.5 text-[11px] text-slate-400">
                        {post.stocks.length ? `${post.stocks.length} 只标的` : '无标签'}
                      </div>
                    </div>

                    <div className="min-w-0 flex-1">
                      {post.text ? (
                        <p className="whitespace-pre-wrap break-words text-[15px] leading-relaxed text-slate-700">
                          {renderText(post.text)}
                        </p>
                      ) : (
                        <p className="text-sm italic text-slate-400">
                          （纯图片帖{post.images > 0 ? `，${post.images} 张配图` : ''}，正文为空）
                        </p>
                      )}

                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        {post.stocks.map((stock) => (
                          <span
                            key={`${post.id}-${stock.code}`}
                            className="inline-flex items-center rounded-full bg-ocean-50 px-2.5 py-0.5 text-xs font-medium text-ocean-700"
                          >
                            {stock.name}
                            {stock.code && <span className="ml-1 text-ocean-400 tabular-nums">{stock.code}</span>}
                          </span>
                        ))}
                        {post.url && (
                          <a
                            href={post.url}
                            target="_blank"
                            rel="noreferrer"
                            className="ml-auto text-xs font-medium text-brand-600 hover:text-brand-700 hover:underline"
                          >
                            查看原帖 ↗
                          </a>
                        )}
                      </div>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
