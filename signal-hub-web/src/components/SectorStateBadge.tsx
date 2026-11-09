/** 板块四态标签；板块为单层结构，各自独立判定，不再有父级差异提示。 */
import type { SectorState } from '../data/types';
import { STATE_LABELS, STATE_NOTES, STATE_TEXT_CLASS } from '../lib/sectorStates';

export function SectorStateBadge({
  state,
  size = 'md',
}: {
  state: SectorState | null;
  size?: 'sm' | 'md';
}) {
  const padding = size === 'sm' ? 'px-2 py-0.5' : 'px-2.5 py-1';
  if (!state) {
    return (
      <span className={`inline-flex items-center ${padding} rounded-full text-xs font-medium bg-slate-100 text-slate-500`}>
        数据不足
      </span>
    );
  }
  return (
    <span
      title={STATE_NOTES[state]}
      className={`inline-flex items-center ${padding} rounded-full text-xs font-medium ${STATE_TEXT_CLASS[state]}`}
    >
      {STATE_LABELS[state]}
    </span>
  );
}
