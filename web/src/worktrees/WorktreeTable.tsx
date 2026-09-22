import { useMemo, useState } from 'react';
import { useWorld } from '../api/useWorld';
import {
  useCloseWorktree,
  useEnvWorktree,
  useForceCloseWorktree,
  useRefreshWorktrees,
  useRemoveWorktree,
  useSyncWorktree,
} from '../api/worktrees';
import type { Worktree } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { listWorktrees } from '../selectors/worktrees';
import { ExternalLink } from '../shell/ExternalLink';
import { PrChip } from '../shell/PrChip';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { ConfirmButton } from '../ui/ConfirmButton';
import styles from './WorktreeTable.module.css';

/**
 * Every worktree across every project, and the operations over them.
 *
 * The one surface that draws a worktree nobody is working in: the canvas can
 * group lanes by worktree, but a lane only holds what is on the desk. Here the
 * rows come from the world, so an idle open worktree is as visible as a busy
 * one — and `mael sync`, `mael close` and `mael env` reach a button rather
 * than only a terminal.
 */
export function WorktreeTable() {
  const { world, status, errors, retry } = useWorld();
  const filters = useAppStore((s) => s.ui.filters);
  const worktreeFilters = useAppStore((s) => s.ui.worktreeFilters);
  const refresh = useRefreshWorktrees();
  // Re-derived only when the world or the filters move, not on every frame
  // the server publishes.
  const groups = useMemo(
    () => listWorktrees(world, filters, worktreeFilters),
    [world, filters, worktreeFilters],
  );

  const terminal =
    status === 'loading' ? (
      <p className={styles.terminal}>Loading…</p>
    ) : status === 'error' ? (
      <p className={styles.terminal} role="alert">
        Could not load the worktrees: {errors[0]?.message ?? 'unknown error'}{' '}
        <AppButton onClick={retry}>Retry</AppButton>
      </p>
    ) : groups.length === 0 ? (
      <p className={styles.terminal}>No worktree matches these filters.</p>
    ) : null;

  return (
    <div className={styles.view} data-testid="worktree-table">
      <div className={styles.toolbar}>
        <AppButton
          variant="quiet"
          processingChildren="Refreshing…"
          onClick={() => refresh.mutateAsync()}
        >
          Refresh
        </AppButton>
      </div>
      {terminal}
      {groups.map((group) => (
        <section key={group.project} className={styles.project}>
          <h2 className={styles.heading}>{group.project}</h2>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>worktree</th>
                <th>branch</th>
                <th>dirty</th>
                <th>local</th>
                <th>remote</th>
                <th>pr</th>
                <th>app</th>
                <th>agents</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {group.rows.map(({ worktree, agents }) => (
                <Row key={worktree.id} worktree={worktree} agents={agents.length} />
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

/** One worktree: what it holds, and what can be done to it. */
function Row({ worktree, agents }: { worktree: Worktree; agents: number }) {
  // One question open at a time in a row: two destructive actions a click
  // apart is how the wrong worktree gets deleted.
  const [asking, setAsking] = useState<'force-close' | 'remove' | null>(null);
  const appUrl = worktree.appRunning && worktree.appUrl ? worktree.appUrl : '';

  return (
    <tr data-worktree-id={worktree.id} data-closed={worktree.isClosed}>
      <td className={styles.mono}>{worktree.nato}</td>
      <td className={styles.mono}>
        {/* A closed worktree carries no branch, and neither does a detached one. */}
        {worktree.branch || <span className={styles.faint}>(detached)</span>}
      </td>
      <td>{worktree.dirtyFiles || ''}</td>
      <td>{worktree.localCommits || ''}</td>
      <td>{remoteCell(worktree)}</td>
      <td>
        <PrChip worktree={worktree} />
      </td>
      <td>{appUrl ? <ExternalLink href={appUrl}>Dev env</ExternalLink> : ''}</td>
      <td>{agents || ''}</td>
      <td className={styles.actions}>
        <Actions worktree={worktree} asking={asking} setAsking={setAsking} />
      </td>
    </tr>
  );
}

/**
 * The commits waiting on the remote.
 *
 * `prCommits` once a pull request is open, `pushedCommits` before one is —
 * which is what "remote branch commits" means for a branch waiting on a new
 * PR. `cli.pr_display` reads the same two, so the table and the terminal give
 * one reading of one branch.
 */
function remoteCell(worktree: Worktree): string {
  const commits = worktree.prNumber ? worktree.prCommits : worktree.pushedCommits;
  return commits ? String(commits) : '';
}

function Actions({
  worktree,
  asking,
  setAsking,
}: {
  worktree: Worktree;
  asking: 'force-close' | 'remove' | null;
  setAsking: (asking: 'force-close' | 'remove' | null) => void;
}) {
  const sync = useSyncWorktree();
  const close = useCloseWorktree();
  const forceClose = useForceCloseWorktree();
  const remove = useRemoveWorktree();
  const env = useEnvWorktree();
  const id: WorktreeId = worktree.id;
  // `_main` holds the project's main checkout, so it never closes and never
  // goes away. It still syncs and still runs an environment.
  const closable = worktree.nato !== '_main';

  return (
    <>
      {!worktree.isClosed && (
        <>
          <AppButton
            variant="quiet"
            title={`Sync ${worktree.nato}`}
            processingChildren="Syncing…"
            onClick={() => sync.mutateAsync({ worktreeId: id, mode: 'autorepair' })}
          >
            Sync
          </AppButton>
          <EnvButton
            worktree={worktree}
            onAct={(action) => env.mutateAsync({ worktreeId: id, action })}
          />
        </>
      )}
      {closable && !worktree.isClosed && (
        <>
          <AppButton
            variant="quiet"
            title={`Close ${worktree.nato}`}
            processingChildren="Closing…"
            onClick={() => close.mutateAsync({ worktreeId: id })}
          >
            Close
          </AppButton>
          <ConfirmButton
            variant="quiet"
            question={`Force close ${worktree.nato}? Work in progress is committed first.`}
            confirm="Force close"
            asking={asking === 'force-close'}
            onAsk={() => setAsking('force-close')}
            onDismiss={() => setAsking(null)}
            // ConfirmButton dismisses itself once the action resolves, and
            // holds the question open showing the error when it rejects.
            onConfirm={() => forceClose.mutateAsync({ worktreeId: id })}
          >
            Force close
          </ConfirmButton>
        </>
      )}
      {closable && (
        <ConfirmButton
          variant="quiet"
          question={`Delete ${worktree.nato}? The checkout goes; the branch stays.`}
          confirm="Delete it"
          asking={asking === 'remove'}
          onAsk={() => setAsking('remove')}
          onDismiss={() => setAsking(null)}
          onConfirm={() => remove.mutateAsync({ worktreeId: id })}
        >
          Delete
        </ConfirmButton>
      )}
    </>
  );
}

/**
 * One control for the environment, reading what is running.
 *
 * Start when it is down, Stop when it is up. Restart is offered beside Stop
 * rather than hidden behind it: it is the common repair, not a rare one.
 */
function EnvButton({
  worktree,
  onAct,
}: {
  worktree: Worktree;
  onAct: (action: 'start' | 'stop' | 'restart') => Promise<unknown>;
}) {
  if (!worktree.appRunning) {
    return (
      <AppButton
        variant="quiet"
        title={`Start the environment in ${worktree.nato}`}
        processingChildren="Starting…"
        onClick={() => onAct('start')}
      >
        Start env
      </AppButton>
    );
  }
  return (
    <>
      <AppButton
        variant="quiet"
        title={`Stop the environment in ${worktree.nato}`}
        processingChildren="Stopping…"
        onClick={() => onAct('stop')}
      >
        Stop env
      </AppButton>
      <AppButton
        variant="quiet"
        title={`Restart the environment in ${worktree.nato}`}
        processingChildren="Restarting…"
        onClick={() => onAct('restart')}
      >
        Restart
      </AppButton>
    </>
  );
}
