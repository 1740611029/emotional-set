import type { ReactNode } from 'react';

export function EmptyState({ text, hint }: { text: string; hint?: string }) {
  return (
    <div className="py-10 text-center">
      <p className="text-sm text-slate-500">{text}</p>
      {hint && <p className="text-xs text-slate-400 mt-1">{hint}</p>}
    </div>
  );
}

export function Notice({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'warn';
  children: ReactNode;
}) {
  const cls =
    tone === 'warn'
      ? 'bg-coral-50 border-coral-100 text-coral-700'
      : 'bg-ocean-50 border-ocean-100 text-ocean-700';
  return <div className={`rounded-xl border px-4 py-3 text-xs leading-relaxed ${cls}`}>{children}</div>;
}

/** 折叠区方向指示箭头：收起朝右、展开旋转 90° 朝下（需求 2026-11-16：原字符箭头过小不明显） */
export function CollapseChevron({ open, className = '' }: { open: boolean; className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden="true"
      className={`w-4 h-4 shrink-0 text-slate-400 transition-transform duration-200 ${open ? 'rotate-90' : ''} ${className}`}
    >
      <path
        fillRule="evenodd"
        d="M6.28 5.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L9.94 10 6.28 6.28a.75.75 0 0 1 0-1.06Z"
        clipRule="evenodd"
      />
    </svg>
  );
}
