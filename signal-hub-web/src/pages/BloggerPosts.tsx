import { useEffect, useMemo, useState } from 'react';
import { EmptyState, Notice } from '../components/ui';
import { PostIcon } from '../components/icons';
import { useBloggerPosts } from '../hooks/useBloggerPosts';
import { usePageMeta } from '../data/PageMetaProvider';
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

/** 配图（CDN 直链热链加载）：加载失败降级为占位提示，不撑破布局 */
function PostImage({ src, alt, className }: { src: string; alt: string; className: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className={`${className} flex items-center justify-center bg-slate-50 text-[11px] text-slate-400`}>
        配图加载失败
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      className={className}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

/** 放大查看器：全屏遮罩 + 大图，支持左右切换、键盘方向键与 ESC、点击遮罩关闭 */
function ImageViewer({
  images,
  index,
  onClose,
  onNavigate,
}: {
  images: string[];
  index: number;
  onClose: () => void;
  onNavigate: (delta: number) => void;
}) {
  const [failed, setFailed] = useState(false);
  const multi = images.length > 1;

  useEffect(() => setFailed(false), [index]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      else if (event.key === 'ArrowLeft') onNavigate(-1);
      else if (event.key === 'ArrowRight') onNavigate(1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, onNavigate]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overscroll-contain bg-slate-900/70 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="发文配图查看"
    >
      <button
        type="button"
        onClick={onClose}
        className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white transition-colors hover:bg-white/20"
        aria-label="关闭大图"
      >
        <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
          <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
        </svg>
      </button>

      {multi && (
        <button
          type="button"
          onClick={(event) => { event.stopPropagation(); onNavigate(-1); }}
          className="absolute left-4 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2.5 text-white transition-colors hover:bg-white/20"
          aria-label="上一张"
        >
          <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M10 3L5 8l5 5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      )}
      {multi && (
        <button
          type="button"
          onClick={(event) => { event.stopPropagation(); onNavigate(1); }}
          className="absolute right-4 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2.5 text-white transition-colors hover:bg-white/20"
          aria-label="下一张"
        >
          <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M6 3l5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      )}

      <div
        className="flex max-h-full max-w-full flex-col items-center gap-3"
        onClick={(event) => event.stopPropagation()}
      >
        {failed ? (
          <div className="rounded-xl bg-white/90 px-8 py-10 text-sm text-slate-500 shadow-2xl">
            配图加载失败，可点击「查看原帖」回到原始页面
          </div>
        ) : (
          <img
            src={images[index]}
            alt={`发文配图 ${index + 1}`}
            className="max-h-[82vh] max-w-[88vw] rounded-xl object-contain shadow-2xl"
            referrerPolicy="no-referrer"
            onError={() => setFailed(true)}
          />
        )}
        {multi && (
          <div className="rounded-full bg-white/10 px-3 py-1 text-xs text-white/90 tabular-nums">
            {index + 1} / {images.length}
          </div>
        )}
      </div>
    </div>
  );
}

/** 放大镜小图标（缩略图 hover 提示） */
function ZoomHintIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L13.5 13.5" strokeLinecap="round" />
      <path d="M7 5.5v3M5.5 7h3" strokeLinecap="round" />
    </svg>
  );
}

export function BloggerPosts() {
  const { data, error, isLoading, isRefreshing, refreshNotice, refresh } = useBloggerPosts();
  const [range, setRange] = useState<RangeKey>('all');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [viewer, setViewer] = useState<{ images: string[]; index: number } | null>(null);

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

  // 页面标题/描述/记录更新/手动刷新上移到 TopBar（需求 2026-11-17），内容区不再渲染 IndicatorHeader
  usePageMeta({
    icon: PostIcon,
    gradientClass: 'from-brand-400 to-mint-400',
    subtitle: '同花顺博主「全能的野人」动态自动采集，只收录最近一个月的公开发文',
    asOf: data?.updated_at ? { text: data.updated_at } : null,
    stats: data ? [{ label: '累计收录', value: `${data.total} 条` }] : undefined,
    refresh: { label: '手动刷新', busyLabel: '采集中…', busy: isRefreshing, onRefresh: refresh },
  });

  return (
    <div className="p-6">

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

      {refreshNotice && (
        <div className="mb-5">
          <Notice tone="warn">手动刷新提示：{refreshNotice}</Notice>
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
            hint="数据由本机计划任务自动采集，采集中请稍候数秒"
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
                          {post.image_urls.length ? '（纯图片帖，正文为空）' : '（正文为空）'}
                        </p>
                      )}

                      {post.image_urls.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {post.image_urls.map((src, imageIndex) => (
                            <button
                              key={src}
                              type="button"
                              onClick={() => setViewer({ images: post.image_urls, index: imageIndex })}
                              className="group relative cursor-zoom-in overflow-hidden rounded-lg border border-slate-100 transition hover:ring-2 hover:ring-brand-200"
                              aria-label={`点击放大查看配图 ${imageIndex + 1}`}
                            >
                              <PostImage
                                src={src}
                                alt={`发文配图 ${imageIndex + 1}`}
                                className={post.image_urls.length === 1 ? 'h-44 w-56 object-cover' : 'h-28 w-28 object-cover'}
                              />
                              <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-slate-900/20 text-white opacity-0 transition-opacity group-hover:opacity-100">
                                <ZoomHintIcon />
                              </span>
                            </button>
                          ))}
                        </div>
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

      {viewer && (
        <ImageViewer
          images={viewer.images}
          index={viewer.index}
          onClose={() => setViewer(null)}
          onNavigate={(delta) => setViewer((current) => current && {
            images: current.images,
            index: (current.index + delta + current.images.length) % current.images.length,
          })}
        />
      )}
    </div>
  );
}
