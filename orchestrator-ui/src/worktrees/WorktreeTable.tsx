import { useMemo, useState } from 'react';
import { useWorld } from '../api/useWorld';
import { useRefreshWorktrees, useRemoveWorktree } from '../api/worktrees';
import type { Worktree } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { worktreePr } from '../selectors/cardPr';
import { listWorktrees } from '../selectors/worktrees';
import { PrChip } from '../shell/PrChip';
import { useLoc } from '../nav/useNav';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { ConfirmButton } from '../ui/ConfirmButton';
import { CloseControl } from './CloseControl';
import { DevEnvLinks } from './DevEnvLinks';
import { EnvControl } from './EnvControl';
import { SyncControl } from './SyncControl';
import styles from './WorktreeTable.module.css';

/**
 * Every worktree across every project, and the operations over them.
 *
 * The rows come from the world, so an idle open worktree is as visible as a
 * busy one, and a closed one can be listed. The canvas draws the open ones as
 * worktree boxes; this is where `mael remove` reaches a button.
 */
export function WorktreeTable() {
  const { world, status, errors, retry } = useWorld();
  const { filters, worktreeFilters } = useLoc();
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
        <AppButton icon={actionIcon('retry')} onClick={retry}>
          Retry
        </AppButton>
      </p>
    ) : groups.length === 0 ? (
      <p className={styles.terminal}>No worktree matches these filters.</p>
    ) : null;

  return (
    <div className={styles.view} data-testid="worktree-table">
      <div className={styles.toolbar}>
        <AppButton
          icon={actionIcon('refresh')}
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
          <h2 className={`${styles.heading} wrap`}>{group.project}</h2>
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
  return (
    <tr data-worktree-id={worktree.id} data-closed={worktree.isClosed}>
      <td className={styles.mono}>{worktree.nato}</td>
      <td className={`${styles.mono} wrap`}>
        {/* A closed worktree carries no branch, and neither does a detached one. */}
        {worktree.branch || <span className={styles.faint}>(detached)</span>}
      </td>
      <td data-label="dirty">{worktree.dirtyFiles || ''}</td>
      <td data-label="local">{worktree.localCommits || ''}</td>
      <td data-label="remote">{remoteCell(worktree)}</td>
      <td>
        <PrChip pr={worktreePr(worktree)} />
      </td>
      <td>
        <DevEnvLinks worktree={worktree} />
      </td>
      <td data-label="agents">{agents || ''}</td>
      <td className={`${styles.actions} nowrap`}>
        <Actions worktree={worktree} />
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

function Actions({ worktree }: { worktree: Worktree }) {
  const [asking, setAsking] = useState(false);
  const remove = useRemoveWorktree();
  const id: WorktreeId = worktree.id;
  // `_main` holds the project's main checkout, so it never closes and never
  // goes away. It still syncs and still runs an environment.
  const closable = worktree.nato !== '_main';

  return (
    <>
      {!worktree.isClosed && (
        <>
          <SyncControl worktree={worktree} />
          <EnvControl worktree={worktree} />
        </>
      )}
      <CloseControl worktree={worktree} />
      {closable && (
        <ConfirmButton
          variant="quiet"
          question={`Delete ${worktree.nato}? The checkout goes; the branch stays.`}
          confirm="Delete it"
          asking={asking}
          onAsk={() => setAsking(true)}
          onDismiss={() => setAsking(false)}
          onConfirm={() => remove.mutateAsync({ worktreeId: id })}
        >
          Delete
        </ConfirmButton>
      )}
    </>
  );
}
