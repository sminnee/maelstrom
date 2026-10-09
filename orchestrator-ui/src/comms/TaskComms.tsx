import { useId, useMemo, useState } from 'react';
import { useComms } from '../api/comms';
import { useUpdateTask } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import type { TaskId } from '../protocol/ids';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { ComboBox } from '../ui/ComboBox';
import fieldStyles from '../ui/Dialog.module.css';
import { useRecent } from '../ui/recent';
import styles from './CommEditor.module.css';

/**
 * The comms a task feeds, and the picker that links another. The task-side twin of the comm
 * editor's `LinkedTasks`, and drawn like it: a link is the task's own `comms` field, written at once and not
 * through the task form's Save.
 */
export function TaskComms({ taskId, held }: { taskId: TaskId; held: string[] }) {
  const { world } = useWorld();
  const comms = useComms().data?.comms;
  const updateTask = useUpdateTask();
  const recent = useRecent('comm');
  const [picked, setPicked] = useState('');
  const pickerId = useId();
  // The world's copy moves when a link is written; the editor's fetched task does not.
  const linked = world.tasks[taskId]?.comms ?? held;
  const byId = useMemo(() => new Map((comms ?? []).map((c) => [c.id, c])), [comms]);
  const options = useMemo(
    () =>
      (comms ?? [])
        .filter((c) => !c.closedAt && !linked.includes(c.id))
        .map((c) => ({ value: c.id, label: c.title })),
    [comms, linked],
  );
  const target = options.some((o) => o.value === picked.trim()) ? picked.trim() : null;

  const setComms = (next: string[]) => updateTask.mutateAsync({ taskId, fields: { comms: next } });

  return (
    <section className={styles.tasks} aria-label="Comms">
      <h3 className={styles.heading}>Comms</h3>
      {linked.length > 0 && (
        <ul className={styles.taskList}>
          {linked.map((id) => (
            <li key={id} className={styles.task} data-comm-id={id}>
              <span className={styles.taskId}>{id}</span>
              <span className={styles.taskTitle}>{byId.get(id)?.title ?? ''}</span>
              <AppButton
                variant="quiet"
                icon={actionIcon('unlink')}
                aria-label={`Unlink ${id}`}
                onClick={() => setComms(linked.filter((c) => c !== id))}
              >
                Unlink
              </AppButton>
            </li>
          ))}
        </ul>
      )}
      <div className={fieldStyles.field}>
        <label htmlFor={pickerId}>Link a comm</label>
        <ComboBox
          id={pickerId}
          value={picked}
          options={options}
          recent={recent.ids}
          onChange={setPicked}
          placeholder="A comm id or title"
        />
      </div>
      <div className={styles.linkRow}>
        <AppButton
          icon={actionIcon('link')}
          disabled={!target}
          onClick={async () => {
            if (!target) return;
            await setComms([...linked, target]);
            recent.touch(target);
            setPicked('');
          }}
        >
          Link
        </AppButton>
      </div>
    </section>
  );
}
