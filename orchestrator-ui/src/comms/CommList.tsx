import { useMemo, useState } from 'react';
import { useComms } from '../api/comms';
import { useWorld } from '../api/useWorld';
import { highestLanding, listComms } from '../selectors/comms';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { CommEditor } from './CommEditor';
import styles from './CommList.module.css';

/** What the editor is open on: a comm, or a new one. */
type Editing = { kind: 'comm'; id: string } | { kind: 'new' } | null;

/** Every open comm, with how far its work has landed. */
export function CommList() {
  const comms = useComms();
  const { world } = useWorld();
  const [showClosed, setShowClosed] = useState(false);
  const [editing, setEditing] = useState<Editing>(null);
  const rows = useMemo(
    () => listComms(comms.data?.comms ?? [], showClosed),
    [comms.data, showClosed],
  );

  return (
    <div className={styles.view} data-testid="comm-list">
      <div className={styles.toolbar}>
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={showClosed}
            onChange={() => setShowClosed((shown) => !shown)}
          />
          <span>Show closed</span>
        </label>
        <AppButton icon={actionIcon('new')} onClick={() => setEditing({ kind: 'new' })}>
          New comm
        </AppButton>
      </div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>title</th>
            <th>recipients</th>
            <th>tasks</th>
            <th>landing</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((comm) => {
            const landing = highestLanding(comm.taskIds.map((id) => world.tasks[id]));
            const open = () => setEditing({ kind: 'comm', id: comm.id });
            return (
              <tr
                key={comm.id}
                data-comm-id={comm.id}
                data-closed={comm.closedAt !== ''}
                onClick={(e) => {
                  if (!(e.target as HTMLElement).closest('button, a')) open();
                }}
              >
                <td>
                  {/* A real button: a row reaches no keyboard. */}
                  <button type="button" className={`${styles.title} wrap`} onClick={open}>
                    {comm.title}
                  </button>
                  {comm.closedAt && <span className={styles.closed}> closed</span>}
                </td>
                <td className={`${styles.recipients} wrap`}>{comm.recipients.join(', ')}</td>
                <td className={`${styles.count} nowrap`}>
                  {comm.taskIds.length === 1 ? '1 task' : `${comm.taskIds.length} tasks`}
                </td>
                <td data-testid="comm-landing" className={landing ? undefined : styles.faint}>
                  {landing ?? 'not done'}
                </td>
              </tr>
            );
          })}
          {comms.isPending && (
            <tr>
              <td colSpan={4} className={styles.faint}>
                Loading…
              </td>
            </tr>
          )}
          {comms.isError && (
            <tr>
              <td colSpan={4} className={styles.faint} role="alert">
                Could not load the comms: {comms.error.message}{' '}
                <AppButton icon={actionIcon('retry')} onClick={() => comms.refetch()}>
                  Retry
                </AppButton>
              </td>
            </tr>
          )}
          {comms.isSuccess && rows.length === 0 && (
            <tr>
              <td colSpan={4} className={styles.faint}>
                {comms.data.comms.length === 0 ? 'No comms yet.' : 'No open comms.'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {editing && (
        <CommEditor
          key={editing.kind === 'comm' ? editing.id : 'new'}
          commId={editing.kind === 'comm' ? editing.id : null}
          onCreated={(id) => setEditing({ kind: 'comm', id })}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
