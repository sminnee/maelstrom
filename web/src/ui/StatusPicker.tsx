import { useState } from 'react';
import type { TaskRow } from '../api/types';
import { TASK_STATUSES, type TaskStatus } from '../protocol/entities';
import styles from './StatusPicker.module.css';

/**
 * A task's status, as text until it is clicked, then a native select. A move
 * the server refuses shows its message beside the control.
 *
 * Native, not a popover: the task list scrolls under `overflow: auto` and the
 * canvas pans and zooms, so a popover would clip or sit in the wrong place.
 *
 * The parent owns which control is picking, because the task list opens one
 * picker at a time across its rows.
 *
 * `label` names the collapsed control for a screen reader. The task list needs
 * none: its column header already says what the word is.
 */
export function StatusPicker({
  task,
  picking,
  onPick,
  onDone,
  onChange,
  className,
  label,
}: {
  task: TaskRow;
  picking: boolean;
  onPick: () => void;
  onDone: () => void;
  onChange: (status: TaskStatus) => void | Promise<unknown>;
  className?: string;
  label?: string;
}) {
  const [error, setError] = useState<string | null>(null);
  const pick = async (status: TaskStatus) => {
    setError(null);
    try {
      await onChange(status);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  if (!picking) {
    return (
      <>
        <button
          type="button"
          className={`${styles.status} ${className ?? ''}`}
          aria-label={label ? `${label}, ${task.status}` : undefined}
          onClick={onPick}
        >
          {task.status}
        </button>
        {error && (
          <span className={styles.error} role="alert">
            {error}
          </span>
        )}
      </>
    );
  }
  return (
    <select
      className={`${styles.statusPicker} ${className ?? ''}`}
      aria-label={`Status of ${task.title}`}
      autoFocus
      value={task.status}
      onChange={(e) => void pick(e.target.value as TaskStatus)}
      // The card listens for Escape on the document to collapse itself, so the
      // key must stop here: one press closes the picker, not the card too.
      onKeyDown={(e) => {
        if (e.key !== 'Escape') return;
        e.stopPropagation();
        onDone();
      }}
      onBlur={onDone}
    >
      {TASK_STATUSES.map((status) => (
        <option key={status} value={status}>
          {status}
        </option>
      ))}
    </select>
  );
}
