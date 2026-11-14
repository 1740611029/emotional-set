/** 定时任务开关 UI：按路由渲染的页面任务条 + 右上角全局开关。 */
import { useLocation } from 'react-router-dom';
import { PAGE_TASKS, TASK_BY_ID, useTaskControl } from '../data/TaskControlProvider';
import type { TaskId } from '../data/TaskControlProvider';

/** 按用户口径：运行中显示「停止定时XXX任务」，已停止显示「开启XXX定时任务」。 */
function taskLabel(name: string, running: boolean): string {
  return running ? `停止定时${name}任务` : `开启${name}定时任务`;
}

function taskTitle(id: TaskId, running: boolean, unavailable: boolean): string {
  const task = TASK_BY_ID[id];
  const suffix = unavailable ? '｜服务端开关不可用，请重启数据服务' : '';
  return `${task.cadence} · ${task.detail}｜当前${running ? '运行中' : '已停止'}${suffix}`;
}

/** 当前页面关联的定时任务；状态全局共享，在任一页面切换都会同步到其他页面与右上角。 */
export function PageTaskBar() {
  const { pathname } = useLocation();
  const tasks = PAGE_TASKS[pathname] ?? [];
  const { enabled, setTask, serverOnline, error } = useTaskControl();

  if (tasks.length === 0) return null;

  return (
    <div className="px-8 pt-6">
      <div className="glass-card flex flex-wrap items-center gap-2 rounded-xl px-4 py-3">
        <span className="mr-1 text-xs font-medium text-slate-500">本页定时任务</span>
        {tasks.map((id) => {
          const running = enabled[id];
          const unavailable = TASK_BY_ID[id].scope === 'server' && !serverOnline;
          return (
            <button
              key={id}
              type="button"
              disabled={unavailable}
              onClick={() => setTask(id, !running)}
              title={taskTitle(id, running, unavailable)}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                running
                  ? 'border-brand-100 bg-brand-50 text-brand-700 hover:bg-brand-100'
                  : 'border-coral-100 bg-coral-50 text-coral-600 hover:bg-coral-100'
              }`}
            >
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${running ? 'bg-mint-500' : 'bg-slate-400'}`} />
              {taskLabel(TASK_BY_ID[id].name, running)}
            </button>
          );
        })}
        {error && <span className="text-xs text-coral-600">{error}</span>}
        <span className="ml-auto text-[11px] text-slate-400">开关仅本次会话有效，刷新页面后恢复全开</span>
      </div>
    </div>
  );
}

/** 全局开关：总闸状态驱动文案，点击后立即翻转；真实运行数单独展示，做到不虚报状态。 */
export function GlobalTaskButton() {
  const { masterEnabled, setAll, runningCount, total, error } = useTaskControl();
  const label = masterEnabled ? '关闭所有定时任务' : '开启所有定时任务';
  const hint = error
    ? `${error}｜当前 ${runningCount}/${total} 个任务仍在运行`
    : `当前 ${runningCount}/${total} 个定时任务运行中；点击${masterEnabled ? '全部关闭' : '全部开启'}（仅本次会话有效）`;

  return (
    <button
      type="button"
      onClick={() => setAll(!masterEnabled)}
      title={hint}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
        masterEnabled
          ? 'border border-brand-100 bg-brand-50 text-brand-700 hover:bg-brand-100'
          : 'bg-brand-500 text-white hover:bg-brand-600'
      }`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${
        error ? 'bg-coral-500' : masterEnabled ? 'bg-mint-500' : 'bg-white'
      }`} />
      {label}
      <span className="text-[10px] opacity-70">{runningCount}/{total}</span>
    </button>
  );
}
