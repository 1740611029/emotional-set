/**
 * 折线图时间滑块（dataZoom）默认区间计算。
 *
 * 口径：默认选中今年；若数据不满今年（最早日期即今年，或全无今年数据）则选全部。
 * 仅计算 ECharts dataZoom 的起始百分比，不裁切、不改动任何原始数值。
 */

/** 返回 dataZoom 的 start 百分比（0 表示全部）。 */
export function thisYearStartPercent(dates: string[]): number {
  if (dates.length <= 1) return 0;
  const year = String(new Date().getFullYear());
  const firstThisYear = dates.findIndex((date) => date.startsWith(year));
  if (firstThisYear <= 0) return 0;

  // 数据总跨度不满一年时，“今年”不足以形成完整视角，直接选全部
  const first = Date.parse(dates[0]);
  const last = Date.parse(dates[dates.length - 1]);
  if (Number.isFinite(first) && Number.isFinite(last) && last - first < 365 * 24 * 60 * 60 * 1000) {
    return 0;
  }

  return (firstThisYear / (dates.length - 1)) * 100;
}
