import { NavLink } from 'react-router-dom';
import type { ComponentType, SVGProps } from 'react';
import { DashboardIcon, SectorIcon, InstitutionIcon, VolumeIcon } from './icons';

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

export interface NavItem {
  key: string;
  label: string;
  to: string;
  icon: IconType;
}

export interface NavGroup {
  section: string;
  items: NavItem[];
}

/**
 * 侧边导航分组。
 *
 * 只收录「有真实数据任务支撑」的页面：每个菜单都必须对应一条已落地的计算链路
 * （指数信号 → index-signals.json、板块趋势 → sector-trends.json）。
 * 无独立计算逻辑的模板页不再占位，避免菜单里出现只有标题不同的重复页面。
 */
export const navGroups: NavGroup[] = [
  {
    section: '概览',
    items: [{ key: 'dashboard', label: '指标总览', to: '/', icon: DashboardIcon }],
  },
  {
    section: '板块',
    items: [{ key: 'sectors', label: '板块趋势', to: '/sectors', icon: SectorIcon }],
  },
  {
    section: '机构持仓',
    items: [{ key: 'institution', label: '机构多空单', to: '/institutions', icon: InstitutionIcon }],
  },
  {
    section: '量能',
    items: [{ key: 'etf-volume', label: 'ETF 放量', to: '/etf-volume', icon: VolumeIcon }],
  },
];

/** 路由 -> 标题映射，供 TopBar 使用 */
export const titleByPath = new Map<string, string>(
  navGroups.flatMap((g) => g.items).map((i) => [i.to, i.label]),
);

export function Sidebar() {
  return (
    <aside className="sidebar w-64 flex-shrink-0 border-r border-slate-100 flex flex-col">
      <div className="p-6 border-b border-slate-100">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-brand-400 to-mint-400 flex items-center justify-center text-white font-bold text-lg shadow-lg shadow-brand-200/50">
            S
          </div>
          <div>
            <h1 className="font-bold text-slate-800 text-lg leading-tight">SignalHub</h1>
            <p className="text-xs text-slate-400">信号指标中心</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto py-4">
        {navGroups.map((group, gi) => (
          <div key={group.section} className={gi === 0 ? '' : 'mt-6'}>
            <div className="px-4 mb-2">
              <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                {group.section}
              </span>
            </div>
            {group.items.map((item) => (
              <NavLink
                key={item.key}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) =>
                  `nav-item flex items-center gap-3 px-4 py-2.5 mx-2 rounded-lg text-sm font-medium ${
                    isActive ? 'active' : 'text-slate-600'
                  }`
                }
              >
                <item.icon className="w-5 h-5" />
                {item.label}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="p-4 border-t border-slate-100">
        <div className="glass-card rounded-xl p-3">
          <div className="flex items-center gap-2 mb-1">
            <div className="w-2 h-2 rounded-full bg-mint-500 animate-pulse"></div>
            <span className="text-xs font-medium text-slate-600">系统状态</span>
          </div>
          <p className="text-xs text-slate-400">数据实时同步中</p>
        </div>
      </div>
    </aside>
  );
}
