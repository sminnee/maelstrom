import { useMemo, useState } from 'react';
import type { TaskRow } from '../api/types';
import { useAddToDesk, useRemoveFromDesk } from '../api/desk';
import { useSetStatus } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import { deskIdForTask } from '../protocol/deskId';
import type { Attention } from '../protocol/attention';
import type { Agent } from '../protocol/entities';
import { TASK_STATUSES, type TaskStatus } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import { filterOptions } from '../selectors/filters';
import { driftLabel, progressOf } from '../protocol/progress';
import { listTasks } from '../selectors/taskList';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { StatusPicker } from '../ui/StatusPicker';
import styles from './TaskList.module.css';

/** Every task in the world, and the one place the desk is edited. */
export function TaskList() {
  const { world, status, errors, retry } = useWorld();
  const filters = useAppStore((s) => s.ui.listFilters);
  const setFilters = useAppStore((s) => s.setListFilters);
  const editTask = useAppStore((s) => s.setEditingTask);
  const addToDesk = useAddToDesk();
  const removeFromDesk = useRemoveFromDesk();
  const setStatus = useSetStatus();
  // Which row's status is being picked.
  const [picking, setPicking] = useState<TaskId | null>(null);
  const attention = useMemo(() => Object.values(world.attention), [world.attention]);
  const options = filterOptions(world, filters);
  // Re-derived only when the world or the filters move, not on every frame
  // the server publishes.
  const rows = useMemo(() => listTasks(world, filters), [world, filters]);

  const toggleStatus = (status: TaskStatus) =>
    setFilters({
      statuses: filters.statuses.includes(status)
        ? filters.statuses.filter((s) => s !== status)
        : [...filters.statuses, status],
    });

  return (
    <div className={styles.view} data-testid="task-list">
      <div className={styles.filters}>
        {TASK_STATUSES.map((status) => (
          <label key={status} className={styles.check}>
            <input
              type="checkbox"
              checked={filters.statuses.includes(status)}
              onChange={() => toggleStatus(status)}
            />
            <span>{status}</span>
          </label>
        ))}
        <label className={styles.field}>
          <span>Project</span>
          <select
            value={filters.project ?? ''}
            onChange={(e) => setFilters({ project: e.target.value || null, branch: null })}
          >
            <option value="">all</option>
            {options.projects.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>Branch</span>
          <select
            value={filters.branch ?? ''}
            onChange={(e) => setFilters({ branch: e.target.value || null })}
          >
            <option value="">all</option>
            {options.branches.map((b) => (
              <option key={b.key} value={b.key}>
                {b.label}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>Search</span>
          <input
            type="search"
            value={filters.text}
            onChange={(e) => setFilters({ text: e.target.value })}
          />
        </label>
      </div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>desk</th>
            <th>id</th>
            <th>title</th>
            <th>project</th>
            <th>branch</th>
            <th>status</th>
            <th>state</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ task, onDesk, agent }) => (
            <tr key={task.id} data-task-id={task.id} data-on-desk={onDesk}>
              <td>
                <AppButton
                  onClick={() =>
                    (onDesk ? removeFromDesk : addToDesk).mutateAsync({
                      id: deskIdForTask(task.id),
                    })
                  }
                >
                  {onDesk ? 'Remove from desk' : 'Add to desk'}
                </AppButton>
                <button type="button" onClick={() => editTask(task.id)}>
                  Edit
                </button>
              </td>
              <td className={styles.mono}>{task.id}</td>
              <td>{task.title}</td>
              <td>{task.project}</td>
              <td className={styles.mono}>{task.branch}</td>
              <td>
                <StatusPicker
                  task={task}
                  picking={picking === task.id}
                  onPick={() => setPicking(task.id)}
                  onDone={() => setPicking(null)}
                  onChange={(status) => {
                    setPicking(null);
                    return setStatus.mutateAsync({ taskId: task.id, status });
                  }}
                />
              </td>
              <td>{stateCell(task, agent, attention)}</td>
            </tr>
          ))}
          {status === 'loading' && (
            <tr>
              <td colSpan={7} className={styles.empty}>
                Loading…
              </td>
            </tr>
          )}
          {status === 'error' && (
            <tr>
              <td colSpan={7} className={styles.empty} role="alert">
                Could not load the tasks: {errors[0]?.message ?? 'unknown error'}{' '}
                <AppButton onClick={retry}>Retry</AppButton>
              </td>
            </tr>
          )}
          {status === 'ready' && rows.length === 0 && (
            <tr>
              <td colSpan={7} className={styles.empty}>
                No task matches these filters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/** The state in words, plus the caret when the status and the agent disagree. */
function stateCell(task: TaskRow, agent: Agent | undefined, attention: readonly Attention[]) {
  const progress = progressOf(task, agent, attention);
  return (
    <>
      {progress.words}
      {progress.drift && (
        <span
          className={styles.drift}
          role="img"
          aria-label={driftLabel(progress)}
          data-drift={progress.drift}
        >
          {' '}
          ▲
        </span>
      )}
    </>
  );
}
