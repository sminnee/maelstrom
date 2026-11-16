import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useComms, useDraftUpdates } from '../api/comms';
import { useWorld } from '../api/useWorld';
import { useHrefFor } from '../nav/useNav';
import { hasDraft, highestLanding, listComms, queueSize } from '../selectors/comms';
import { noFilters } from '../selectors/filters';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { useCommDetail } from '../nav/useOverlays';
import { CommChip } from './CommChip';
import { CommEditor } from './CommEditor';
import styles from './CommList.module.css';

/** Every open comm, with how far its work has landed. */
export function CommList() {
  const comms = useComms();
  const { world } = useWorld();
  const [showClosed, setShowClosed] = useState(false);
  // A new comm's form is the list's own; a comm's editor is the location's.
  const [creating, setCreating] = useState(false);
  const detail = useCommDetail();
  const rows = useMemo(
    () => listComms(comms.data?.comms ?? [], showClosed),
    [comms.data, showClosed],
  );
  const draftUpdates = useDraftUpdates();
  const queued = queueSize(comms.data?.comms ?? []);

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
        <div className={styles.actions}>
          <AppButton
            icon={actionIcon('edit')}
            disabled={queued === 0}
            title={queued === 0 ? 'No comm has news yet' : undefined}
            onClick={() => draftUpdates.mutateAsync()}
          >
            {queued === 0 ? 'Draft updates' : `Draft updates (${queued})`}
          </AppButton>
          <AppButton icon={actionIcon('new')} onClick={() => setCreating(true)}>
            New comm
          </AppButton>
        </div>
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
            const open = () => detail.open(comm.id);
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
                  <CommChip comm={comm} className={styles.chip} />
                  {/* A real button: a row reaches no keyboard. */}
                  <button type="button" className={`${styles.title} wrap`} onClick={open}>
                    {comm.title}
                  </button>
                  {comm.closedAt && <span className={styles.closed}> closed</span>}
                  {hasDraft(comm) && <span className={styles.draft}> draft</span>}
                </td>
                <td className={`${styles.recipients} wrap`}>{comm.recipients.join(', ')}</td>
                <td className={`${styles.count} nowrap`}>
                  <TasksLink comm={comm.id} count={comm.taskIds.length} />
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
      {creating && (
        <CommEditor
          commId={null}
          onCreated={(id) => {
            setCreating(false);
            detail.open(id);
          }}
          onClose={() => setCreating(false)}
        />
      )}
    </div>
  );
}

/**
 * The comm's task count, as a link to Tasks filtered to them alone: the other filters clear, so
 * the list holds every task the count names. Every status: they are mostly done.
 */
function TasksLink({ comm, count }: { comm: string; count: number }) {
  const to = useHrefFor({
    view: 'list',
    card: null,
    filters: { ...noFilters(), comm },
    listFilters: { statuses: [] },
  });
  return <Link to={to}>{count === 1 ? '1 task' : `${count} tasks`}</Link>;
}
