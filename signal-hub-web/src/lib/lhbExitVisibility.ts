/**
 * 【龙虎榜知名游资】离场配对展示时效：纯函数，无 DOM、无请求、无副作用。
 *
 * 口径沿革：
 * - 2026-11-14：游资总览按 5 个交易日阈值过滤，起算日为 positions[].last_seen；
 * - 2026-11-16：切换为 hideAllExited 一律隐藏（打标签即隐藏）；
 * - 2026-11-17（当前口径，用户确认）：
 *   1. 离场标签出现后 5 个交易日内：始终显示并带徽章（开关不影响）；
 *   2. 超过 5 个交易日：默认隐藏，「显示 已离场超5日个股」开关回显（带徽章）；
 *   3. 起算日改为离场标签判定成立日 positions[].exit_date（「打标签当天开始算」）；
 *   4. 离场隐藏仅作用于游资总览 tab（含详情弹窗）；每日动向与个股游资总览
 *      传 includeExpired: true 全量展示、全量带徽章，不做任何离场过滤。
 *
 * 产物数据完整保留，本模块只决定页面可见性，沿用「展示什么就统计什么」。
 */
import type { LhbOverviewTrader, LhbPosition } from '../data/types';

/** 离场配对自动隐藏的交易日阈值（2026-11-17 口径：标签出现后超过 5 个交易日才隐藏） */
export const EXIT_VISIBILITY_DAYS = 5;

/** 展示层游资视图：在产物游资上附加「离场配对」索引（key=个股代码） */
export type ViewTrader = LhbOverviewTrader & { exits: Map<string, LhbPosition> };

export interface BuildViewOptions {
  /** true = 回显离场超 5 个交易日的配对（开关开启）；false = 默认隐藏。≤5 日的离场配对不受开关影响、始终显示 */
  includeExpired: boolean;
}

export interface BuildViewResult {
  traders: ViewTrader[];
  /** 离场超 5 个交易日「默认隐藏」的配对涉及的去重个股代码；与开关状态无关 */
  hiddenCodes: Set<string>;
}

/**
 * 离场标签日（exit_date）距 as_of 的交易日数；日期无法可靠定位时返回 null，调用方按保守展示处理。
 * 2026-11-17 口径：按用户「打标签当天开始算」，起算日从 last_seen 改为 exit_date。
 * 只用产物已有的交易日序列，不插值、不外推交易日历。
 */
export function exitAgeInTradingDays(
  position: LhbPosition, tradingDates: string[], asOf: string | null,
): number | null {
  if (!asOf || !position.exit_date) return null;
  const i = tradingDates.indexOf(position.exit_date);
  const j = tradingDates.indexOf(asOf);
  if (i < 0 || j < 0 || j < i) return null;
  return j - i;
}

/**
 * 展示层「在场过滤」：从原始产物独立构建一份视图。
 * 规则（2026-11-17 用户确认）：
 * - active / 无 position 记录 → 展示，无徽章；
 * - 已离场且 age ≤ EXIT_VISIBILITY_DAYS → 展示 + 徽章（开关不影响）；
 * - 已离场且 age > EXIT_VISIBILITY_DAYS → includeExpired 决定展示（带徽章）或隐藏；
 * - age 无法计算（日期异常）→ 保守展示 + 徽章。
 * 返回 hiddenCodes 供按钮显示；该集合恒表示「默认隐藏」的超期配对，切换开关不改变它。
 */
export function buildViewTraders(
  rawTraders: LhbOverviewTrader[],
  tradingDates: string[],
  asOf: string | null,
  options: BuildViewOptions,
): BuildViewResult {
  const { includeExpired } = options;
  const round2 = (value: number) => Math.round(value * 100) / 100;
  const result: ViewTrader[] = [];
  const hiddenCodes = new Set<string>();

  for (const trader of rawTraders) {
    const exits = new Map<string, LhbPosition>();
    const records = trader.records.filter((record) => {
      const position = trader.positions?.find((item) => item.code === record.code);
      if (!position || position.status === 'active') return true;
      const age = exitAgeInTradingDays(position, tradingDates, asOf);
      if (age !== null && age > EXIT_VISIBILITY_DAYS) {
        hiddenCodes.add(record.code);
        if (includeExpired) {
          exits.set(record.code, position);
          return true;
        }
        return false;
      }
      // age ≤ 5 或日期异常：展示 + 徽章
      exits.set(record.code, position);
      return true;
    });
    // 原有记录但全部配对已超期隐藏 → 整卡隐藏；开关开启时不触发
    if (!includeExpired && trader.records.length > 0 && records.length === 0) continue;
    // 操作记录统一按日期倒序（新→旧），同日按净额绝对值倒序
    records.sort((a, b) => b.date.localeCompare(a.date) || Math.abs(b.net) - Math.abs(a.net));
    // 统计口径与管道一致：只累计单日榜；展示什么就统计什么（超期隐藏同步剔除）
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
