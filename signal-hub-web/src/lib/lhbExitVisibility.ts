/**
 * 【龙虎榜知名游资】离场配对展示时效：纯函数，无 DOM、无请求、无副作用。
 *
 * 口径（2026-11-14 用户确认）：
 * - 游资总览 + 个股游资总览：统一 5 个交易日，第 6 个交易日起默认隐藏，可切换回显；
 * - 每日动向：保持既有 3 个交易日过滤口径，不参与开关；
 * - 起算日为产物 positions[].last_seen（该游资在该股最后一次上单日榜的日期），
 *   按 trading_dates 序号差计交易日；
 * - 产物数据完整保留，本模块只决定页面可见性，沿用「展示什么就统计什么」。
 *
 * 两份视图各自直接从原始产物构建，互不复用结果：5 日视图不经过 3 日裁剪，
 * 每日动向视图不消费显示开关。
 */
import type { LhbOverviewTrader, LhbPosition } from '../data/types';

/** 游资总览 + 个股游资总览：离场配对展示时效（交易日） */
export const EXIT_VISIBILITY_DAYS = 5;
/** 每日动向：既有展示口径（交易日），保持不变 */
export const DAILY_VIEW_MARK_DAYS = 3;

/** 展示层游资视图：在产物游资上附加「离场标记期内配对」索引（key=个股代码） */
export type ViewTrader = LhbOverviewTrader & { exits: Map<string, LhbPosition> };

export interface BuildViewOptions {
  /** 距 exit_date 超过该交易日数即默认隐藏 */
  hideAfterDays: number;
  /** true = 回显超期配对（显示开关开启）；false = 默认隐藏 */
  includeExpired: boolean;
}

export interface BuildViewResult {
  traders: ViewTrader[];
  /** 按 hideAfterDays 阈值「默认会被隐藏」的配对涉及的去重个股代码；与开关状态无关 */
  hiddenCodes: Set<string>;
}

/**
 * last_seen 距 as_of 的交易日数；日期无法可靠定位时返回 null，调用方按保守展示处理。
 * 只用产物已有的交易日序列，不插值、不外推交易日历。
 */
export function exitAgeInTradingDays(
  position: LhbPosition, tradingDates: string[], asOf: string | null,
): number | null {
  if (!asOf || !position.last_seen) return null;
  const i = tradingDates.indexOf(position.last_seen);
  const j = tradingDates.indexOf(asOf);
  if (i < 0 || j < 0 || j < i) return null;
  return j - i;
}

/**
 * 展示层「在场过滤」：从原始产物独立构建一份视图。
 * 返回 hiddenCodes 供按钮显示精确计数；该集合恒表示「默认隐藏」的个股，切换开关不改变它。
 */
export function buildViewTraders(
  rawTraders: LhbOverviewTrader[],
  tradingDates: string[],
  asOf: string | null,
  options: BuildViewOptions,
): BuildViewResult {
  const { hideAfterDays, includeExpired } = options;
  const round2 = (value: number) => Math.round(value * 100) / 100;
  const result: ViewTrader[] = [];
  const hiddenCodes = new Set<string>();

  for (const trader of rawTraders) {
    const exits = new Map<string, LhbPosition>();
    const records = trader.records.filter((record) => {
      const position = trader.positions?.find((item) => item.code === record.code);
      if (!position || position.status === 'active' || !position.last_seen) return true;
      const age = exitAgeInTradingDays(position, tradingDates, asOf);
      // 日期异常 → 保守展示，且不打离场标记（沿用既有行为）
      if (age === null) return true;
      if (age > hideAfterDays) {
        hiddenCodes.add(record.code);
        if (includeExpired) {
          exits.set(record.code, position);
          return true;
        }
        return false;
      }
      exits.set(record.code, position);
      return true;
    });
    // 原有记录但全部配对已离场超期 → 整卡隐藏；开关开启时不触发
    if (!includeExpired && trader.records.length > 0 && records.length === 0) continue;
    // 操作记录统一按日期倒序（新→旧），同日按净额绝对值倒序
    records.sort((a, b) => b.date.localeCompare(a.date) || Math.abs(b.net) - Math.abs(a.net));
    // 统计口径与管道一致：只累计单日榜；展示什么就统计什么（离场隐藏同步剔除）
    const single = records.filter((record) => !record.is_multi_day);
    const stats = {
      active_days: new Set(single.map((record) => record.date)).size,
      records: single.length,
      stocks: new Set(single.map((record) => record.code)).size,
      buy_total: round2(single.reduce((sum, record) => sum + record.buy, 0)),
      sell_total: round2(single.reduce((sum, record) => sum + record.sell, 0)),
      net_total: round2(single.reduce((sum, record) => sum + record.net, 0)),
    };
    result.push({ ...trader, records, stats, exits });
  }
  return { traders: result, hiddenCodes };
}
