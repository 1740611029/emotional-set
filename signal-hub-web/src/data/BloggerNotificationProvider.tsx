import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { fetchBloggerPosts } from './client';
import { useTaskControl } from './TaskControlProvider';
import type { BloggerPost } from './types';

/**
 * 【全能的野人】新发文的桌面通知（网页侧）。
 *
 * 与采集脚本侧 Windows Toast 双通道并行（2026-11-17 起）：两侧各自弹出、互不依赖。
 * 采集脚本始终发系统 Toast，本模块在页面在线且已授权时弹浏览器通知。
 * 单侧让位架构已废弃：Windows 系统层可单独关闭 Chrome 应用通知而页面 JS 无法感知，
 * 让位会造成双方都不发的通知黑洞。
 *
 * 本模块不参与任何信号计算，只读 /api/blogger/posts 落盘数据。
 * 轮询刻意不受页面可见性影响，保证网页最小化时仍能弹出系统通知。
 */
const POLL_MS = 30_000;
const HEARTBEAT_MS = 30_000;
export const BLOGGER_NOTIFY_TAG = 'signalhub-blogger';

type PermissionState = 'granted' | 'denied' | 'default' | 'unsupported';

interface BloggerNotificationValue {
  permission: PermissionState;
  enabled: boolean;
  /** 必须在用户点击手势中调用，浏览器才允许申请通知权限；返回授权结果供 UI 给出反馈 */
  enable: () => Promise<PermissionState>;
  test: () => void;
}

const BloggerNotificationContext = createContext<BloggerNotificationValue | null>(null);

function isSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}

function readPermission(): PermissionState {
  return isSupported() ? Notification.permission : 'unsupported';
}

function postBody(post: BloggerPost): string {
  const labels = post.stocks.length
    ? post.stocks.map((stock) => stock.name).join('、')
    : '无个股标签';
  const plain = (post.text || (post.images > 0 ? '（纯图片帖）' : '（正文为空）'))
    .replace(/\s+/g, ' ')
    .trim();
  const snippet = plain.length > 70 ? `${plain.slice(0, 70)}…` : plain;
  return `${post.date} ${post.time} ｜ ${labels}\n${snippet}`;
}

function showNotification(title: string, body: string) {
  try {
    const notification = new Notification(title, { body, tag: BLOGGER_NOTIFY_TAG });
    notification.onclick = () => {
      window.focus();
      window.location.assign('/blogger');
      notification.close();
    };
  } catch {
    /* 权限被系统回收或浏览器限制时静默失败，采集脚本会按不在线兜底 */
  }
}

export function BloggerNotificationProvider({ children }: { children: ReactNode }) {
  const [permission, setPermission] = useState<PermissionState>(readPermission);
  const permissionRef = useRef<PermissionState>(permission);
  const seenRef = useRef<Set<string> | null>(null);
  const heartbeatOkRef = useRef(false);
  const aliveRef = useRef(true);
  const { enabled: taskEnabled } = useTaskControl();
  const notifyEnabled = taskEnabled.blogger_notify;
  permissionRef.current = permission;

  const heartbeat = useCallback(async (online: boolean) => {
    const notify = online && permissionRef.current === 'granted';
    try {
      const response = await fetch('/api/blogger/present', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ online, notify }),
        cache: 'no-store',
        keepalive: true,
      });
      // 服务未重启时该端点尚不存在（404）：本次心跳记为失败；
      // 心跳失败不影响浏览器通知弹出（采集侧 Toast 双通道并行，不互斥）。
      heartbeatOkRef.current = response.ok;
    } catch {
      heartbeatOkRef.current = false;
      /* 心跳失败时采集脚本按「网页不在线」兜底发 Toast，不会漏提醒 */
    }
  }, []);

  const enable = useCallback(async (): Promise<PermissionState> => {
    if (!isSupported()) return 'unsupported';
    const result = await Notification.requestPermission();
    permissionRef.current = result;
    setPermission(result);
    // 立即同步在线状态，避免采集脚本在心跳周期内仍按旧状态重复弹通知
    await heartbeat(true);
    return result;
  }, [heartbeat]);

  const test = useCallback(() => {
    if (permissionRef.current !== 'granted') return;
    showNotification('SignalHub 桌面提醒已开启', '【全能的野人】有新发文时会在这里提醒你');
  }, []);

  const poll = useCallback(async () => {
    try {
      const data = await fetchBloggerPosts();
      if (!aliveRef.current) return;
      const ids = new Set(data.posts.map((post) => post.id));
      const seen = seenRef.current;
      if (seen === null) {
        // 首次加载建立已读基线：已收录的历史发文一律不弹，避免刷屏
        seenRef.current = ids;
        return;
      }
      const fresh = data.posts.filter((post) => !seen.has(post.id));
      for (const post of fresh) seen.add(post.id);
      if (!fresh.length) return;
      if (permissionRef.current !== 'granted' || !heartbeatOkRef.current) return;
      const latest = fresh[0];
      const title = fresh.length === 1
        ? '【全能的野人】新发文'
        : `【全能的野人】新增 ${fresh.length} 条发文`;
      showNotification(title, postBody(latest));
    } catch {
      /* 请求失败保留基线，下一轮重试，不伪造内容 */
    }
  }, []);

  // 首次取数建立基线、上报在线状态、注册卸载钩子：与定时开关无关
  useEffect(() => {
    aliveRef.current = true;
    void poll();
    void heartbeat(true);
    const onPageHide = () => {
      // sendBeacon 在标签页关闭/浏览器退出时比 fetch 可靠：立即置离线，让采集侧接管
      if (typeof navigator.sendBeacon === 'function') {
        const blob = new Blob(
          [JSON.stringify({ online: false, notify: false })],
          { type: 'application/json' },
        );
        navigator.sendBeacon('/api/blogger/present', blob);
      }
    };
    window.addEventListener('pagehide', onPageHide);
    return () => {
      aliveRef.current = false;
      window.removeEventListener('pagehide', onPageHide);
    };
  }, [heartbeat, poll]);

  // 定时轮询 + 在线心跳受「新发文提醒」任务开关控制：关闭时不再创建任何定时器
  useEffect(() => {
    if (!notifyEnabled) {
      // 恢复时重建已读基线，避免把停止期间累积的发文一次性全部弹出
      seenRef.current = null;
      return undefined;
    }
    const pollTimer = window.setInterval(() => void poll(), POLL_MS);
    const beatTimer = window.setInterval(() => void heartbeat(true), HEARTBEAT_MS);
    return () => {
      window.clearInterval(pollTimer);
      window.clearInterval(beatTimer);
    };
  }, [notifyEnabled, poll, heartbeat]);

  const value: BloggerNotificationValue = {
    permission,
    enabled: permission === 'granted',
    enable,
    test,
  };

  return (
    <BloggerNotificationContext.Provider value={value}>
      {children}
    </BloggerNotificationContext.Provider>
  );
}

export function useBloggerNotifications(): BloggerNotificationValue {
  const value = useContext(BloggerNotificationContext);
  if (!value) {
    throw new Error('useBloggerNotifications 必须在 BloggerNotificationProvider 内使用');
  }
  return value;
}
