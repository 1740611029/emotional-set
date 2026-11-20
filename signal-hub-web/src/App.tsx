import { Suspense, lazy } from 'react';
import { BrowserRouter, Navigate, Routes, Route } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { TaskControlProvider } from './data/TaskControlProvider';
import { PageMetaProvider } from './data/PageMetaProvider';
import { BloggerNotificationProvider } from './data/BloggerNotificationProvider';

/** 图表页按路由懒加载以控制首屏体积 */
const InstitutionPositions = lazy(() => import('./pages/InstitutionPositions').then((module) => ({ default: module.InstitutionPositions })));
const EtfVolume = lazy(() => import('./pages/EtfVolume').then((module) => ({ default: module.EtfVolume })));
const BloggerPosts = lazy(() => import('./pages/BloggerPosts').then((module) => ({ default: module.BloggerPosts })));
const LhbTraders = lazy(() => import('./pages/LhbTraders').then((module) => ({ default: module.LhbTraders })));
const LimitCycle = lazy(() => import('./pages/LimitCycle').then((module) => ({ default: module.LimitCycle })));

export default function App() {
  return (
    <TaskControlProvider>
      <PageMetaProvider>
        <BloggerNotificationProvider>
          <BrowserRouter>
            <div className="flex h-screen overflow-hidden">
              <Sidebar />
              <main className="flex-1 overflow-hidden flex flex-col">
                <TopBar />
                {/* flex flex-col：长内容页（/lhb）以 flex-1 锁定视口剩余高度；块级页面行为不变 */}
                <div className="flex-1 min-h-0 overflow-y-auto flex flex-col">
                  <Suspense fallback={<div className="p-6 text-sm text-slate-500">加载中…</div>}>
                    <Routes>
                      {/* 指数总览 / 板块趋势已下线：根路径指向发文页，旧书签回落到首页避免空白 */}
                      <Route path="/" element={<Navigate to="/blogger" replace />} />
                      <Route path="/sectors" element={<Navigate to="/blogger" replace />} />
                      <Route path="/limit-cycle" element={<LimitCycle />} />
                      <Route path="/institutions" element={<InstitutionPositions />} />
                      <Route path="/etf-volume" element={<EtfVolume />} />
                      <Route path="/blogger" element={<BloggerPosts />} />
                      <Route path="/lhb" element={<LhbTraders />} />
                      <Route path="/institution" element={<Navigate to="/institutions" replace />} />
                      <Route path="*" element={<Navigate to="/blogger" replace />} />
                    </Routes>
                  </Suspense>
                </div>
              </main>
            </div>
          </BrowserRouter>
        </BloggerNotificationProvider>
      </PageMetaProvider>
    </TaskControlProvider>
  );
}
