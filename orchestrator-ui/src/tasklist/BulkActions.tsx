import { useState } from 'react';
import { useAddToDesk, useRemoveFromDesk } from '../api/desk';
import { useSetStatus } from '../api/tasks';
import { deskIdForTask } from '../protocol/deskId';
import { TASK_STATUSES, type TaskStatus } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import type { ListRow } from '../selectors/taskList';
import { AppButton } from '../ui/AppButton';
import { messageOf } from '../ui/useClickLifecycle';
import styles from './BulkActions.module.css';

/** One change applied to every ticked row of the task list. */
export function BulkActions({
  rows,
  failure,
  onDone,
  onClear,
}: {
  rows: ListRow[];
  /** Why the last run failed, kept by the list because a tick outdates it. */
  failure: string | null;
  /** Called after a run with the tasks now in the target state, and why the rest are not. */
  onDone: (done: TaskId[], failure: string | null) => void;
  onClear: () => void;
}) {
  const addToDesk = useAddToDesk();
  const removeFromDesk = useRemoveFromDesk();
  const setStatus = useSetStatus();
  const [busy, setBusy] = useState(false);

  // One at a time: each status write re-reads the notebook, and parallel
  // re-reads race. A refused row does not stop the others.
  const run = async (targets: ListRow[], send: (row: ListRow) => Promise<unknown>) => {
    // A row the run skips is already in the target state, so it is done too.
    const done = new Set(rows.map((r) => r.task.id));
    let failed = 0;
    let why = '';
    setBusy(true);
    for (const row of targets) {
      try {
        await send(row);
      } catch (err) {
        done.delete(row.task.id);
        failed += 1;
        why ||= messageOf(err);
      }
    }
    setBusy(false);
    onDone([...done], failed ? `${failed} of ${targets.length} failed: ${why}` : null);
  };

  return (
    <section className={styles.bulk} aria-label="Bulk actions">
      <span>{rows.length} selected</span>
      {/* Applies on change, so the value always rests on the placeholder. */}
      <select
        aria-label="Set status"
        value=""
        disabled={busy}
        onChange={(e) => {
          const status = e.target.value as TaskStatus;
          void run(
            rows.filter((r) => r.task.status !== status),
            (r) => setStatus.mutateAsync({ taskId: r.task.id, status }),
          );
        }}
      >
        <option value="" disabled>
          Set status…
        </option>
        {TASK_STATUSES.map((status) => (
          <option key={status} value={status}>
            {status}
          </option>
        ))}
      </select>
      <AppButton
        disabled={busy}
        onClick={() =>
          run(
            rows.filter((r) => !r.onDesk),
            (r) => addToDesk.mutateAsync({ id: deskIdForTask(r.task.id) }),
          )
        }
      >
        Add to desk
      </AppButton>
      {/* Only the rows on the desk: a DELETE for one that is not is a 404. */}
      <AppButton
        disabled={busy}
        onClick={() =>
          run(
            rows.filter((r) => r.onDesk),
            (r) => removeFromDesk.mutateAsync({ id: deskIdForTask(r.task.id) }),
          )
        }
      >
        Remove from desk
      </AppButton>
      <AppButton disabled={busy} onClick={onClear}>
        Clear
      </AppButton>
      {failure && (
        <span className={styles.failure} role="alert">
          {failure}
        </span>
      )}
    </section>
  );
}
