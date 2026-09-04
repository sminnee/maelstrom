import { useCallback, useEffect, useRef, useState } from 'react';
import { useTask, useUpdateTask } from '../api/tasks';
import type { TaskEdit } from '../api/types';
import type { Task } from '../protocol/entities';
import type { PermissionMode } from '../protocol/modes';
import { MODES } from '../protocol/modes';
import type { TaskId } from '../protocol/ids';
import { KNOWN_COMMANDS } from '../protocol/phase';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import styles from './TaskEditor.module.css';

/** From `task.PRIORITIES`, highest first. */
const PRIORITIES = ['critical', 'high', 'medium', 'low'];

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
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close(null);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [close]);
  return (
    <div className={styles.scrim} onMouseDown={() => close(null)}>
      <div
        className={styles.editor}
        role="dialog"
        aria-label={taskId}
        data-testid="task-editor-wait"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <p role={error ? 'alert' : undefined}>
          {error ? `Could not load ${taskId}: ${error}` : 'Loading…'}
        </p>
        <footer className={styles.footer}>
          <button type="button" onClick={() => close(null)}>
            Cancel
          </button>
          {error && <AppButton onClick={retry}>Retry</AppButton>}
        </footer>
      </div>
    </div>
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
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => box.current?.focus({ preventScroll: true }), []);

  // The content field shows the whole task body: it grows to fit, and the
  // editor scrolls. Sized on mount and after each edit, because `scrollHeight`
  // is only known once the browser has laid the text out.
  const content = useRef<HTMLTextAreaElement>(null);
  const grow = useCallback((el: HTMLTextAreaElement | null) => {
    content.current = el;
    fitToText(el);
  }, []);
  useEffect(() => fitToText(content.current), [draft.content]);

  // Leaving with unsaved edits asks first: the content field holds the task's
  // whole body, and a stray click on the scrim would otherwise lose it.
  const leave = useCallback(() => {
    if (Object.keys(changed(opened.current, draft)).length === 0) close(null);
    else setConfirming(true);
  }, [close, draft]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && leave();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [leave]);

  const set = (patch: Partial<typeof draft>) => setDraft((d) => ({ ...d, ...patch }));

  const save = async () => {
    const fields = changed(opened.current, draft);
    // Nothing moved: the same close as Cancel, rather than a refused command.
    if (Object.keys(fields).length === 0) return close(null);
    await update.mutateAsync({ taskId: task.id, fields });
    close(null);
  };

  return (
    <div className={styles.scrim} onMouseDown={leave}>
      <div
        ref={box}
        className={styles.editor}
        role="dialog"
        aria-label={task.title}
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className={styles.header}>
          <h2 className={styles.heading}>{task.notebookId}</h2>
          <button type="button" className={styles.close} aria-label="Close" onClick={leave}>
            ×
          </button>
        </header>

        <label className={styles.field}>
          <span>Title</span>
          <input value={draft.title} onChange={(e) => set({ title: e.target.value })} />
        </label>
        <label className={styles.field}>
          <span>Content</span>
          <textarea
            ref={grow}
            rows={1}
            value={draft.content}
            onChange={(e) => set({ content: e.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span>Branch</span>
          <input value={draft.branch} onChange={(e) => set({ branch: e.target.value })} />
        </label>

        <details className={styles.advanced}>
          <summary>Advanced</summary>
          <label className={styles.field}>
            <span>Command</span>
            {/* Free-form in the notebook, so this offers the known ones and
                keeps anything else typed. Empty runs the task itself. */}
            <input
              list="task-commands"
              value={draft.command}
              onChange={(e) => set({ command: e.target.value })}
            />
            <datalist id="task-commands">
              <option value="" />
              {KNOWN_COMMANDS.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </label>
          <label className={styles.field}>
            <span>Mode</span>
            <select
              value={draft.mode}
              onChange={(e) => set({ mode: e.target.value as PermissionMode })}
            >
              {MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span>Priority</span>
            <select value={draft.priority} onChange={(e) => set({ priority: e.target.value })}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span>Model</span>
            <input value={draft.model} onChange={(e) => set({ model: e.target.value })} />
          </label>
        </details>

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
        <footer className={styles.footer}>
          <button type="button" onClick={leave}>
            Cancel
          </button>
          <AppButton variant="primary" onClick={save}>
            Save
          </AppButton>
        </footer>
      </div>
    </div>
  );
}

/** Set a textarea's height to the height of its text. */
function fitToText(el: HTMLTextAreaElement | null) {
  if (!el) return;
  // Collapse first, or the height only ever grows: `scrollHeight` includes
  // whatever height is already set.
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
}

/** Every editable field of the task, as the form holds them. */
function seed(task: Task): Required<TaskEdit> {
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
function changed(before: Required<TaskEdit>, after: Required<TaskEdit>): TaskEdit {
  const fields: TaskEdit = {};
  for (const key of Object.keys(before) as (keyof TaskEdit)[]) {
    if (after[key] !== before[key]) Object.assign(fields, { [key]: after[key] });
  }
  return fields;
}
