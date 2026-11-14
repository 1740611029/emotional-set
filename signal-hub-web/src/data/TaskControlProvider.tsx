/** 定时任务开关中心：登记网页侧轮询与服务端调度分支，供页面按钮和右上角全局按钮共用。 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

export type TaskId =
  | 'snapshot_poll' | 'blogger_notify'
  | 'live_quote' | 'close_rebuild' | 'evening_backfill' | 'lhb_daily';

export interface TaskDef {
  id: TaskId;
  /** 按钮文案里的 XXX：定时任务的内容 */
  name: string;
  scope: 'web' | 'server';
  cadence: string;
  detail: string;
}

export const TASKS: TaskDef[] = [
  {
    id: 'snapshot_poll', name: '刷新行情快照', scope: 'web', cadence: '每 30 秒',
    detail: '浏览器轮询 /api/snapshot，更新顶部时间与总览、板块数据',
  },
  {
    id: 'blogger_notify', name: '新发文提醒', scope: 'web', cadence: '每 30 秒',
    detail: '浏览器轮询博主发文并上报在线心跳；停止后桌面提醒由采集脚本 Toast 兜底',
  },
  {
    id: 'live_quote', name: '盘中实时快照', scope: 'server', cadence: '交易时段每 30 秒',
    detail: '服务端拉取实时行情并生成盘中快照',
  },
  {
    id: 'close_rebuild', name: '收盘日线重算', scope: 'server', cadence: '每交易日 17:00',
    detail: '刷新指数与板块日线并重算信号产物',
  },
  {
    id: 'evening_backfill', name: '晚间数据补齐', scope: 'server', cadence: '17:30 / 20:00 / 22:00',
    detail: '补齐发布较晚的同花顺行业指数日线',
  },
  {
    id: 'lhb_daily', name: '龙虎榜游资日更', scope: 'server', cadence: '20:00 / 22:00',
    detail: '更新 lhb-traders.json 展示产物',
  },
];

export const TASK_BY_ID = Object.fromEntries(
  TASKS.map((task) => [task.id, task]),
) as Record<TaskId, TaskDef>;

export const TASK_IDS: TaskId[] = TASKS.map((task) => task.id);
export const WEB_TASK_IDS: TaskId[] = TASKS.filter((task) => task.scope === 'web').map((task) => task.id);
export const SERVER_TASK_IDS: TaskId[] = TASKS.filter((task) => task.scope === 'server').map((task) => task.id);

/** 每个页面关联的定时任务：任务多为全局常驻，这里列出本页数据链路上会跑到的分支。 */
export const PAGE_TASKS: Record<string, TaskId[]> = {
  '/': ['snapshot_poll', 'live_quote', 'close_rebuild'],
  '/sectors': ['snapshot_poll', 'close_rebuild', 'evening_backfill'],
  '/institutions': ['snapshot_poll'],
  '/etf-volume': ['snapshot_poll'],
  '/blogger': ['snapshot_poll', 'blogger_notify'],
  '/lhb': ['snapshot_poll', 'lhb_daily'],
  '/limit-cycle': ['snapshot_poll'],
};

interface TaskControlValue {
  enabled: Record<TaskId, boolean>;
  setTask: (id: TaskId, enabled: boolean) => void;
  setAll: (enabled: boolean) => void;
  /** 全局总闸状态：按钮文案由它驱动，点击后立即翻转，不依赖后端请求是否成功 */
  masterEnabled: boolean;
  allEnabled: boolean;
  allDisabled: boolean;
  runningCount: number;
  total: number;
  serverOnline: boolean;
  error: string | null;
}

const INITIAL_ENABLED = Object.fromEntries(
  TASK_IDS.map((id) => [id, true]),
) as Record<TaskId, boolean>;

const TaskControlContext = createContext<TaskControlValue | null>(null);

async function postTaskSwitch(task: string, enabled: boolean): Promise<Record<string, boolean>> {
  const response = await fetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task, enabled }),
    cache: 'no-store',
  });
  if (!response.ok) {
    let detail = `任务开关接口返回 ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      /* 响应体非 JSON 时保留状态码文案 */
    }
    throw new Error(detail);
  }
  const data = await response.json();
  return (data?.tasks ?? {}) as Record<string, boolean>;
}

function mergeServerStates(
  prev: Record<TaskId, boolean>,
  states: Record<string, boolean> | null | undefined,
): Record<TaskId, boolean> {
  const next = { ...prev };
  if (!states) return next;
  for (const id of SERVER_TASK_IDS) {
    if (typeof states[id] === 'boolean') next[id] = states[id];
  }
  return next;
}

export function TaskControlProvider({ children }: { children: ReactNode }) {
  // 状态只在本次会话内有效：刷新页面或重启服务一律恢复全开，避免"忘了开过"导致数据长期不更新
  const [enabled, setEnabled] = useState<Record<TaskId, boolean>>(INITIAL_ENABLED);
  const [masterEnabled, setMasterEnabled] = useState(true);
  const [serverOnline, setServerOnline] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const readServerStates = useCallback(async (): Promise<Record<string, boolean>> => {
    const response = await fetch('/api/tasks', {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      throw new Error(`任务状态接口返回 ${response.status}（请重启数据服务）`);
    }
    const data = await response.json();
    return (data?.tasks ?? {}) as Record<string, boolean>;
  }, []);

  // 页面加载时探测一次；服务未重启时给出明确提示，而不是让按钮静默失效
  useEffect(() => {
    let alive = true;
    void readServerStates()
      .then((states) => {
        if (!alive) return;
        setEnabled((prev) => mergeServerStates(prev, states));
        setServerOnline(true);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!alive) return;
        setServerOnline(false);
        setError(cause instanceof Error ? cause.message : '任务状态读取失败');
      });
    return () => { alive = false; };
  }, [readServerStates]);

  // 每次操作都重新探测：服务在页面打开之后才重启，也能自动恢复，无需手动刷新
  const ensureServerOnline = useCallback(async (): Promise<boolean> => {
    if (serverOnline) return true;
    try {
      const states = await readServerStates();
      setEnabled((prev) => mergeServerStates(prev, states));
      setServerOnline(true);
      setError(null);
      return true;
    } catch (cause) {
      setServerOnline(false);
      setError(cause instanceof Error ? cause.message : '任务状态读取失败');
      return false;
    }
  }, [serverOnline, readServerStates]);

  const setTask = useCallback((id: TaskId, next: boolean) => {
    const task = TASK_BY_ID[id];
    if (!task) return;
    if (task.scope === 'web') {
      setEnabled((prev) => ({ ...prev, [id]: next }));
      return;
    }
    void (async () => {
      if (!(await ensureServerOnline())) return;
      try {
        const states = await postTaskSwitch(id, next);
        setEnabled((prev) => mergeServerStates(prev, states));
        setError(null);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '任务开关请求失败');
      }
    })();
  }, [ensureServerOnline]);

  const setAll = useCallback((next: boolean) => {
    // 立即反馈：总闸翻转 + 网页侧两个轮询真实启停（定时器会立即创建或清理）
    setMasterEnabled(next);
    setEnabled((prev) => {
      const updated = { ...prev };
      for (const id of WEB_TASK_IDS) updated[id] = next;
      return updated;
    });

    void (async () => {
      // 服务端 4 个调度分支必须真的切换；任一步失败都整体回滚，绝不虚报"已开启/已关闭"
      let applied = false;
      try {
        if (await ensureServerOnline()) {
          const states = await postTaskSwitch('all', next);
          if (SERVER_TASK_IDS.every((id) => states[id] === next)) {
            setEnabled((prev) => mergeServerStates(prev, states));
            setError(null);
            applied = true;
          } else {
            setError('服务端返回的任务状态与目标不一致，请刷新页面核对');
          }
        }
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '任务开关请求失败');
      }

      if (!applied) {
        // 回滚到点击前：网页侧轮询与总闸一起回退，再按服务端真实状态校准
        const previous = !next;
        setMasterEnabled(previous);
        setEnabled((prev) => {
          const updated = { ...prev };
          for (const id of WEB_TASK_IDS) updated[id] = previous;
          return updated;
        });
        try {
          const states = await readServerStates();
          setEnabled((prev) => mergeServerStates(prev, states));
          setServerOnline(true);
        } catch {
          setServerOnline(false);
        }
      }
    })();
  }, [ensureServerOnline, readServerStates]);

  // 逐项把任务全部关掉 / 全部打开时，总闸也要跟着变，保持「都关闭了显示开启」的口径
  useEffect(() => {
    const running = TASK_IDS.filter((id) => enabled[id]).length;
    if (running === 0) setMasterEnabled(false);
    else if (running === TASK_IDS.length) setMasterEnabled(true);
  }, [enabled]);

  const value = useMemo<TaskControlValue>(() => {
    const runningCount = TASK_IDS.filter((id) => enabled[id]).length;
    return {
      enabled,
      setTask,
      setAll,
      masterEnabled,
      runningCount,
      total: TASK_IDS.length,
      allEnabled: runningCount === TASK_IDS.length,
      allDisabled: runningCount === 0,
      serverOnline,
      error,
    };
  }, [enabled, setTask, setAll, masterEnabled, serverOnline, error]);

  return <TaskControlContext.Provider value={value}>{children}</TaskControlContext.Provider>;
}

export function useTaskControl(): TaskControlValue {
  const value = useContext(TaskControlContext);
  if (!value) {
    throw new Error('useTaskControl 必须在 TaskControlProvider 内使用');
  }
  return value;
}
