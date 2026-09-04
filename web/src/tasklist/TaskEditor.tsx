import { useCallback, useRef, useState } from 'react';
import { useTask, useUpdateTask } from '../api/tasks';
import type { TaskEdit } from '../api/types';
import type { Task } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
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
  const [draft, setDraft] = useState(() => seed(task));
  const [confirming, setConfirming] = useState(false);
  // Frozen: the store's copy moves as the server publishes, and diffing
  // against a moved copy would send a field the user never touched.
  const opened = useRef(draft);

  // Leaving with unsaved edits asks first: the content field holds the task's
  // whole body, and a stray click on the scrim would otherwise lose it.
  const leave = useCallback(() => {
    if (Object.keys(changed(opened.current, draft)).length === 0) close(null);
    else setConfirming(true);
  }, [close, draft]);

  const set = (patch: Partial<TaskDraft>) => setDraft((d) => ({ ...d, ...patch }));

  const save = async () => {
    const fields = changed(opened.current, draft);
    // Nothing moved: the same close as Cancel, rather than a refused command.
    if (Object.keys(fields).length === 0) return close(null);
    await update.mutateAsync({ taskId: task.id, fields });
    close(null);
  };

  return (
    <Dialog label={task.title} onClose={leave}>
      <DialogHeader title={task.notebookId} onClose={leave} />
      <TaskFields draft={draft} onChange={set} />

      {confirming && (
        <p className={styles.confirm} role="alert">
          <span>Throw away your changes?</span>
          <button type="button" onClick={() => setConfirming(false)}>
            Keep editing
          </button>
          <button type="button" onClick={() => close(null)}>
            Discard
          </button>
        </p>
      )}
      <DialogFooter>
        <button type="button" onClick={leave}>
          Cancel
        </button>
        <AppButton variant="primary" onClick={save}>
          Save
        </AppButton>
      </DialogFooter>
    </Dialog>
  );
}

/** Every editable field of the task, as the form holds them. */
function seed(task: Task): TaskDraft {
  return {
    title: task.title,
    content: task.content,
    branch: task.branch,
    command: task.command,
    mode: task.mode,
    priority: task.priority,
    model: task.model,
  };
}

/** Only what the user moved. */
function changed(before: TaskDraft, after: TaskDraft): TaskEdit {
  const fields: TaskEdit = {};
  for (const key of Object.keys(before) as (keyof TaskEdit)[]) {
    if (after[key] !== before[key]) Object.assign(fields, { [key]: after[key] });
  }
  return fields;
}
