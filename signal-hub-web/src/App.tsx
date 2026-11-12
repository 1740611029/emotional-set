import { Suspense, lazy } from 'react';
import { BrowserRouter, Navigate, Routes, Route } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { MarketDataProvider } from './data/MarketDataProvider';
import { BloggerNotificationProvider } from './data/BloggerNotificationProvider';
import { Dashboard } from './pages/Dashboard';

/** 板块页含图表库，按路由懒加载以控制首屏体积 */
const Sectors = lazy(() => import('./pages/Sectors').then((module) => ({ default: module.Sectors })));
const InstitutionPositions = lazy(() => import('./pages/InstitutionPositions').then((module) => ({ default: module.InstitutionPositions })));
const EtfVolume = lazy(() => import('./pages/EtfVolume').then((module) => ({ default: module.EtfVolume })));
const BloggerPosts = lazy(() => import('./pages/BloggerPosts').then((module) => ({ default: module.BloggerPosts })));
const LhbTraders = lazy(() => import('./pages/LhbTraders').then((module) => ({ default: module.LhbTraders })));

export default function App() {
  return (
    <BloggerNotificationProvider>
      <MarketDataProvider>
        <BrowserRouter>
          <div className="flex h-screen overflow-hidden">
            <Sidebar />
            <main className="flex-1 overflow-hidden flex flex-col">
              <TopBar />
              <div className="flex-1 min-h-0 overflow-y-auto">
                <Suspense fallback={<div className="p-8 text-sm text-slate-500">加载中…</div>}>
                  <Routes>
                    <Route path="/" element={<Dashboard />} />
                    <Route path="/sectors" element={<Sectors />} />
                    <Route path="/institutions" element={<InstitutionPositions />} />
                    <Route path="/etf-volume" element={<EtfVolume />} />
                    <Route path="/blogger" element={<BloggerPosts />} />
                    <Route path="/lhb" element={<LhbTraders />} />
                    <Route path="/institution" element={<Navigate to="/institutions" replace />} />
                    {/* 已下线的指标页地址回落到总览，避免旧书签打开空白页 */}
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </Suspense>
              </div>
            </main>
          </div>
        </BrowserRouter>
      </MarketDataProvider>
    </BloggerNotificationProvider>
  );
}
