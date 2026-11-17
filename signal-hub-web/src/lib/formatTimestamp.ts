/**
 * 统一时间戳展示格式化（需求 2026-11-17：TopBar「最近更新」出现 +08:00 时区尾巴）。
 *
 * 后端时间戳存在两种口径：
 * - ISO 带 T 带时区：`2026-11-17T13:49:01+08:00`（server/snapshot.now_iso、institution_positions）
 * - ISO 带 T 无时区：`2026-11-16T22:07:22`（部分落盘产物）
 * 统一归一为「YYYY-MM-DD HH:MM:SS」纯文本。刻意不做 Date 解析，避免浏览器时区
 * 转换引入偏差——只做字符串层面的裁剪。
 */
export function formatTimestamp(value: string | null | undefined): string | null {
  if (!value) return null;
  // 去掉时区尾巴（+08:00 / -08:00 / Z），再把 ISO 的 T 换成空格
  return value.replace(/(?:[+-]\d{2}:\d{2}|Z)$/, '').replace('T', ' ');
}
