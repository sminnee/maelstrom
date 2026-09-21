import { useCallback, useMemo, useRef, useState } from 'react';
import { ApiError, describeError } from '../api/http';
import { useDeleteTask, useSetStatus, useTask, useUpdateTask } from '../api/tasks';
import type { TaskEdit } from '../api/types';
import { useWorld } from '../api/useWorld';
import type { Task } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import { listTasks } from '../selectors/taskList';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { ConfirmButton } from '../ui/ConfirmButton';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import type { TaskDraft } from './TaskFields';
import { TaskFields } from './TaskFields';
import styles from './TaskEditor.module.css';

/**
 * Edits one task's fields. The list holds slim rows, so the editor fetches
 * the task's prose itself and opens once it has it.
 */
export function TaskEditor({ taskId }: { taskId: TaskId }) {
  const task = useTask(taskId);
  if (task.data) return <TaskForm key={task.data.id} task={task.data} />;
  return (
    <WaitShell
      taskId={taskId}
      error={task.isError ? task.error.message : null}
      retry={() => void task.refetch()}
    />
  );
}

/** The editor before its task has arrived: a way out on Escape and a button, and a Retry on failure. */
function WaitShell({
  taskId,
  error,
  retry,
}: {
  taskId: TaskId;
  error: string | null;
  retry: () => void;
}) {
  const close = useAppStore((s) => s.setEditingTask);
  const leave = useCallback(() => close(null), [close]);
  return (
    <Dialog label={taskId} onClose={leave} testId="task-editor-wait">
      <p role={error ? 'alert' : undefined}>
        {error ? `Could not load ${taskId}: ${error}` : 'Loading…'}
      </p>
      <DialogFooter>
        <button type="button" onClick={leave}>
          Cancel
        </button>
        {error && <AppButton onClick={retry}>Retry</AppButton>}
      </DialogFooter>
    </Dialog>
  );
}

function TaskForm({ task }: { task: Task }) {
  const close = useAppStore((s) => s.setEditingTask);
  const update = useUpdateTask();
  const setStatus = useSetStatus();
  const remove = useDeleteTask();
  const [draft, setDraft] = useState(() => seed(task));
  const [confirming, setConfirming] = useState(false);
  const [pendingNav, setPendingNav] = useState<TaskId | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  // Frozen: the store's copy moves as the server publishes, and diffing
  // against a moved copy would send a field the user never touched.
  const opened = useRef(draft);

  const { world } = useWorld();
  const filters = useAppStore((s) => s.ui.filters);
  const listFilters = useAppStore((s) => s.ui.listFilters);
  const { prevId, nextId } = useMemo(() => {
    const rows = listTasks(world, filters, listFilters);
    const index = rows.findIndex((r) => r.task.id === task.id);
    return {
      prevId: index > 0 ? rows.at(index - 1)?.task.id : undefined,
      nextId: index >= 0 && index < rows.length - 1 ? rows.at(index + 1)?.task.id : undefined,
    };
  }, [world, filters, listFilters, task.id]);

  // A function, not a render-time value: `opened` is a ref, and reading it
  // during render (rather than from an event handler) is unsafe even though
  // this one is frozen after mount.
  const dirty = useCallback(
    () => editing && Object.keys(changed(opened.current, draft)).length > 0,
    [editing, draft],
  );

  // Leaving with unsaved edits asks first: the content field holds the task's
  // whole body, and a stray click on the scrim would otherwise lose it. A
  // read-only dialog has nothing to lose, so it always closes at once.
  const leave = useCallback(() => {
    if (!dirty()) close(null);
    else setConfirming(true);
  }, [close, dirty]);

  // Prev/Next go through the same guard as the ×/Escape/backdrop: a dirty
  // draft asks before it is dropped, whichever adjacent id it is dropped for.
  const go = useCallback(
    (id: TaskId | undefined) => {
      if (id === undefined) return;
      if (!dirty()) close(id);
      else setPendingNav(id);
    },
    [close, dirty],
  );

  const set = (patch: Partial<TaskDraft>) => setDraft((d) => ({ ...d, ...patch }));

  const save = async () => {
    const { status, ...fields } = changed(opened.current, draft);
    const writes: Promise<unknown>[] = [];
    if (Object.keys(fields).length > 0) writes.push(update.mutateAsync({ taskId: task.id, fields }));
    if (status !== undefined) writes.push(setStatus.mutateAsync({ taskId: task.id, status }));
    // Nothing moved: the same close as Cancel, rather than a refused command.
    if (writes.length === 0) return close(null);
    await Promise.all(writes);
    close(null);
  };

  const destroy = async () => {
    await remove.mutateAsync({ taskId: task.id });
    close(null);
  };

  // A refused delete leaves the dialog open, so the reason is shown rather
  // than left to the button's own "Failed".
  const [deleteError, setDeleteError] = useState<string | null>(null);

  return (
    <Dialog label={task.title} onClose={leave}>
      <DialogHeader title={task.notebookId} onClose={leave}>
        <button type="button" disabled={prevId === undefined} onClick={() => go(prevId)}>
          ‹ Prev
        </button>
        <button type="button" disabled={nextId === undefined} onClick={() => go(nextId)}>
          Next ›
        </button>
      </DialogHeader>
      <TaskFields
        draft={draft}
        onChange={set}
        project={task.project}
        taskId={task.id}
        bucket={task.notebookId}
        readOnly={!editing}
      />

      {(confirming || pendingNav !== null) && (
        <p className={styles.confirm} role="alert">
          <span>Throw away your changes?</span>
          <button
            type="button"
            onClick={() => {
              setConfirming(false);
              setPendingNav(null);
            }}
          >
            Keep editing
          </button>
          <button type="button" onClick={() => close(pendingNav)}>
            Discard
          </button>
        </p>
      )}
      {deleteError && (
        <p className={styles.confirm} role="alert">
          {deleteError}
        </p>
      )}
      <DialogFooter>
        {editing ? (
          <>
            <button type="button" onClick={leave}>
              Cancel
            </button>
            <AppButton variant="primary" onClick={save}>
              Save
            </AppButton>
          </>
        ) : (
          <>
            <button type="button" onClick={() => close(null)}>
              Close
            </button>
            <ConfirmButton
              question="Delete this task?"
              confirm="Delete it"
              asking={confirmingDelete}
              onAsk={() => {
                setDeleteError(null);
                setConfirmingDelete(true);
              }}
              onDismiss={() => setConfirmingDelete(false)}
              onConfirm={destroy}
              // The server's own words, not the button's "Failed": a refused
              // delete names what is holding the task, and that is what the
              // user has to act on.
              onError={(err) =>
                setDeleteError(err instanceof ApiError ? err.message : describeError(err))
              }
            >
              Delete
            </ConfirmButton>
            <AppButton variant="primary" onClick={() => setEditing(true)}>
              Edit
            </AppButton>
          </>
        )}
      </DialogFooter>
    </Dialog>
  );
}

/** Every editable field of the task, as the form holds them. */
function seed(task: Task): TaskDraft {
  return {
    title: task.title,
    content: task.content,
    status: task.status,
    branch: task.branch,
    command: task.command,
    mode: task.mode,
    priority: task.priority,
    model: task.model,
    executeModel: task.executeModel,
    follows: task.follows,
  };
}

/** Only what the user moved. `follows` is an array, so `!==` always differs. */
function changed(before: TaskDraft, after: TaskDraft): TaskEdit {
  const fields: TaskEdit = {};
  for (const key of Object.keys(before) as (keyof TaskDraft)[]) {
    if (key === 'follows') {
      if (!sameIds(before.follows, after.follows)) fields.follows = after.follows;
      continue;
    }
    if (after[key] !== before[key]) Object.assign(fields, { [key]: after[key] });
  }
  return fields;
}

/** Whether two id lists hold the same ids, order aside. */
function sameIds(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort();
  const sortedB = [...b].sort();
  return sortedA.every((id, i) => id === sortedB[i]);
}
