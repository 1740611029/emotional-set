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

export const DashboardIcon = (p: P) => (
  <Svg {...p}>
    <path d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
  </Svg>
);

export const MaCrossIcon = (p: P) => (
  <Svg {...p}>
    <path d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
  </Svg>
);

export const MomentumIcon = (p: P) => (
  <Svg {...p}>
    <path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6m14 0v-6a2 2 0 012-2h2a2 2 0 012 2v6m-6 0V9a2 2 0 012-2h2a2 2 0 012 2v10" />
  </Svg>
);

export const BollingerIcon = (p: P) => (
  <Svg {...p}>
    <path d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
  </Svg>
);

export const VolumeIcon = (p: P) => (
  <Svg {...p}>
    <path d="M15 10l4.553-2.276A1 1 0 012.1 8.618v6.764a1 1 0 01-1.447.894L1   15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
  </Svg>
);

export const DrawdownIcon = (p: P) => (
  <Svg {...p}>
    <path d="M19 14l-7 7m0 0l-7-7m7 7V3" />
  </Svg>
);

export const CompositeIcon = (p: P) => (
  <Svg {...p}>
    <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
  </Svg>
);

export const IndicesIcon = (p: P) => (
  <Svg {...p}>
    <path d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 00110.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064" />
  </Svg>
);

export const SectorIcon = (p: P) => (
  <Svg {...p}>
    <path d="M4 20V10m5 10V4m5 16v-7m5 7V8" />
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

export const PieChartIcon = (p: P) => (
  <Svg strokeWidth={1.5} {...p}>
    <path d="M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z" />
    <path d="M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z" />
  </Svg>
);

export const TrendChartIcon = (p: P) => (
  <Svg strokeWidth={1.5} {...p}>
    <path d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
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
