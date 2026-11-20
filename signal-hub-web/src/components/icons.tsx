import type { SVGProps } from 'react';

/**
 * 图标集合，与设计稿 signal-dashboard/index.html 中的 SVG path 一一对应。
 * 统一 stroke=currentColor，尺寸由外部 className 控制。
 */
type P = SVGProps<SVGSVGElement>;

function Svg({ children, ...props }: P & { children: React.ReactNode }) {
  return (
    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" {...props}>
      {children}
    </svg>
  );
}

export const VolumeIcon = (p: P) => (
  <Svg {...p}>
    <path d="M15 10l4.553-2.276A1 1 0 012.1 8.618v6.764a1 1 0 01-1.447.894L1   15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
  </Svg>
);

export const SearchIcon = (p: P) => (
  <Svg {...p}>
    <path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
  </Svg>
);

export const BellIcon = (p: P) => (
  <Svg {...p}>
    <path d="M15 17h5l-1.405-1.405A2.032 2.032 0 011.595 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C77.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
  </Svg>
);

export const InstitutionIcon = (p: P) => (
  <Svg {...p}>
    <path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6m14 0v-6a2 2 0 012-2h2a2 2 0 012 2v6m-6 0V9a2 2 0 012-2h2a2 2 0 012 2v10" />
  </Svg>
);

/** 博主动态：气泡消息图标 */
export const PostIcon = (p: P) => (
  <Svg {...p}>
    <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2v10z" />
    <path d="M8 8h8M8 12h5" />
  </Svg>
);

/** 龙虎榜：领奖台榜单图标 */
export const LhbIcon = (p: P) => (
  <Svg {...p}>
    <path d="M8 21h8M12 17v4M7 4h10v4a5 5 0 01-10 0V4z" />
    <path d="M7 5H4v2a3 3 0 003 3M17 5h3v2a3 3 0 01-3 3" />
  </Svg>
);

/** 连板周期：递升的连板高度 + 情绪波动 */
export const LimitCycleIcon = (p: P) => (
  <Svg {...p}>
    <path d="M3 20h18" />
    <path d="M5 20v-4h3v4M10 20v-8h3v8M15 20v-12h3v12" />
    <path d="M3 7l3-3 3 2 3-4 3 3 3-2 3 1" />
  </Svg>
);
