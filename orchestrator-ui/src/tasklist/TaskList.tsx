import { useEffect, useMemo, useRef, useState } from 'react';
import type { TaskRow } from '../api/types';
import { useAddToDesk, useRemoveFromDesk } from '../api/desk';
import { useDeleteTask, useSetStatus } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import { deskIdForTask } from '../protocol/deskId';
import type { Attention } from '../protocol/attention';
import type { Agent } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import { driftLabel, progressOf } from '../protocol/progress';
import { listTasks } from '../selectors/taskList';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { ConfirmButton } from '../ui/ConfirmButton';
import { StatusPicker } from '../ui/StatusPicker';
import { BulkActions } from './BulkActions';
import styles from './TaskList.module.css';

/** Every task in the world, and the one place the desk is edited. */
export function TaskList() {
  const { world, status, errors, retry } = useWorld();
  const filters = useAppStore((s) => s.ui.filters);
  const listFilters = useAppStore((s) => s.ui.listFilters);
  const editTask = useAppStore((s) => s.setEditingTask);
  const editingTaskId = useAppStore((s) => s.ui.editingTaskId);
  const addToDesk = useAddToDesk();
  const removeFromDesk = useRemoveFromDesk();
  const setStatus = useSetStatus();
  const deleteTask = useDeleteTask();
  // Which row's status is being picked.
  const [picking, setPicking] = useState<TaskId | null>(null);
  // Which row is asking whether to delete.
  const [deleting, setDeleting] = useState<TaskId | null>(null);
  const attention = useMemo(() => Object.values(world.attention), [world.attention]);
  // Re-derived only when the world or the filters move, not on every frame
  // the server publishes.
  const rows = useMemo(() => listTasks(world, filters, listFilters), [world, filters, listFilters]);
  // The ticked rows, only ever listed ones: a task the filter hides is
  // unticked, so the bar never acts on a row the user cannot see.
  const [ticked, setTicked] = useState<ReadonlySet<TaskId>>(new Set());
  // Why the last bulk run failed. It describes that run's rows, so any tick clears it.
  const [failure, setFailure] = useState<string | null>(null);
  // Pruned while rendering, not in an effect, so no frame draws a hidden tick.
  const [prunedFor, setPrunedFor] = useState(rows);
  if (prunedFor !== rows) {
    setPrunedFor(rows);
    const listed = new Set(rows.map((r) => r.task.id));
    if (![...ticked].every((id) => listed.has(id))) {
      setTicked(new Set([...ticked].filter((id) => listed.has(id))));
    }
  }
  const selected = rows.filter((r) => ticked.has(r.task.id));
  const allTicked = rows.length > 0 && selected.length === rows.length;
  const tickAll = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (tickAll.current) tickAll.current.indeterminate = selected.length > 0 && !allTicked;
  }, [selected.length, allTicked]);
  const retick = (change: (prev: ReadonlySet<TaskId>) => ReadonlySet<TaskId>) => {
    setFailure(null);
    setTicked(change);
  };
  const toggle = (taskId: TaskId) =>
    retick((prev) => {
      const next = new Set(prev);
      if (!next.delete(taskId)) next.add(taskId);
      return next;
    });

  return (
    <div className={styles.view} data-testid="task-list">
      {selected.length > 0 && (
        <BulkActions
          rows={selected}
          failure={failure}
          // Unticks only the rows that went through, so a row ticked during
          // the run stays ticked.
          onDone={(done, why) => {
            setTicked((prev) => new Set([...prev].filter((id) => !done.includes(id))));
            setFailure(why);
          }}
          onClear={() => retick(() => new Set())}
        />
      )}
      <table className={styles.table}>
        <thead>
          <tr>
            <th>
              <input
                ref={tickAll}
                type="checkbox"
                aria-label="Select all"
                checked={allTicked}
                onChange={() => retick(() => new Set(allTicked ? [] : rows.map((r) => r.task.id)))}
              />
            </th>
            <th>id</th>
            <th>title</th>
            <th>project</th>
            <th>branch</th>
            <th>status</th>
            <th>state</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map(({ task, onDesk, agent }) => (
            <tr
              key={task.id}
              data-task-id={task.id}
              data-on-desk={onDesk}
              // A click on a control in the row is that control's, not the
              // row's. Asked of the target rather than stopped per cell, so a
              // control added later needs no guard of its own.
              onClick={(e) => {
                if (!(e.target as HTMLElement).closest('button, select, input, a')) {
                  editTask(task.id);
                }
              }}
            >
              <td>
                <input
                  type="checkbox"
                  aria-label={`Select ${task.id}`}
                  checked={ticked.has(task.id)}
                  onChange={() => toggle(task.id)}
                />
              </td>
              <td className={styles.mono}>{task.id}</td>
              <td>
                {/* A real button: a row reaches no keyboard. */}
                <button type="button" className={styles.title} onClick={() => editTask(task.id)}>
                  {task.title}
                </button>
              </td>
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
                {/* One question open at a time: two rows asking at once is two
                    destructive actions one click apart. */}
                <ConfirmButton
                  question="Delete this task?"
                  confirm="Delete it"
                  asking={deleting === task.id}
                  onAsk={() => setDeleting(task.id)}
                  onDismiss={() => setDeleting(null)}
                  onConfirm={async () => {
                    await deleteTask.mutateAsync({ taskId: task.id });
                    // The dialog mounts from the store, so a delete that
                    // leaves it open refetches a task that is gone.
                    if (editingTaskId === task.id) editTask(null);
                  }}
                >
                  Delete
                </ConfirmButton>
              </td>
            </tr>
          ))}
          {status === 'loading' && (
            <tr>
              <td colSpan={8} className={styles.empty}>
                Loading…
              </td>
            </tr>
          )}
          {status === 'error' && (
            <tr>
              <td colSpan={8} className={styles.empty} role="alert">
                Could not load the tasks: {errors[0]?.message ?? 'unknown error'}{' '}
                <AppButton onClick={retry}>Retry</AppButton>
              </td>
            </tr>
          )}
          {status === 'ready' && rows.length === 0 && (
            <tr>
              <td colSpan={8} className={styles.empty}>
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
