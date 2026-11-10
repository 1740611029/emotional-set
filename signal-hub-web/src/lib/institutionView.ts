/**
 * 机构多空单页展示辅助：固定展示顺序 + 滑动窗口同向率。
 *
 * 纯函数、只读：展示顺序只重排既有机构元数据，不改动任何数值；
 * 窗口同向率只读取已落盘的 relation 序列，口径与整段统计保持一致——
 * 同向（S）/对冲（H）计入分母，待判定（P）与无数据（N）不计入分母。
 */
import type {
  InstitutionId,
  InstitutionMeta,
  InstitutionRelation,
  InstitutionStat,
} from '../data/types';

/** 固定展示顺序：中信期货 → 国泰君安 → 其他机构（中信在前）。 */
export const INSTITUTION_DISPLAY_ORDER: InstitutionId[] = ['citic', 'guotai', 'others'];

/** 按固定顺序重排机构元数据；未知 id 按原始顺序追加在末尾，保证不丢项、不改数值。 */
export function orderInstitutions(institutions: InstitutionMeta[]): InstitutionMeta[] {
  const byId = new Map(institutions.map((meta) => [meta.id, meta]));
  const ordered: InstitutionMeta[] = [];
  for (const id of INSTITUTION_DISPLAY_ORDER) {
    const meta = byId.get(id);
    if (meta) ordered.push(meta);
  }
  for (const meta of institutions) {
    if (!INSTITUTION_DISPLAY_ORDER.includes(meta.id)) ordered.push(meta);
  }
  return ordered;
}

/**
 * 滑动窗口同向率：取 relation 尾部 window 个交易日，按与整段统计一致的口径汇总。
 *
 * - 待判定 / 无数据不计入分母（与页面既有「待判定不计入同向率分母」口径一致）；
 * - 窗口不足时按实际可用天数计算，并用该窗口内已判定交易日的真实起止区间标注；
 * - small_sample 阈值与整段统计一致（已判定样本 < 30）。
 */
export function windowRelationStats(
  relation: InstitutionRelation[],
  dates: string[],
  window: number,
): InstitutionStat {
  const length = Math.min(relation.length, dates.length);
  const startIndex = Math.max(0, length - window);
  let same = 0;
  let hedge = 0;
  let pending = 0;
  let noData = 0;
  let start: string | null = null;
  let end: string | null = null;
  for (let index = startIndex; index < length; index += 1) {
    const code = relation[index];
    if (code === 'S') {
      same += 1;
    } else if (code === 'H') {
      hedge += 1;
    } else if (code === 'P') {
      pending += 1;
    } else {
      noData += 1;
    }
    if (code === 'S' || code === 'H') {
      if (start === null) start = dates[index];
      end = dates[index];
    }
  }
  const decided = same + hedge;
  return {
    rate: decided ? same / decided : null,
    decided,
    same,
    hedge,
    pending,
    no_data: noData,
    small_sample: decided < 30,
    start,
    end,
  };
}
