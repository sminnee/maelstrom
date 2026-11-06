import { useMemo } from 'react';
import { useSystem } from '../api/system';
import { useWorld } from '../api/useWorld';
import type { ProjectTab } from '../nav/location';
import { useNewWork, useProjectDetail } from '../nav/useOverlays';
import type { ProjectId } from '../protocol/ids';
import { hasUserTasks, listProjects, projectPaths } from '../selectors/projects';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import { WorktreeTable } from '../worktrees/WorktreeTable';
import { useSetUpProject } from './useSetUpProject';
import styles from './ProjectDetail.module.css';

const TABS: { id: ProjectTab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'worktrees', label: 'Worktrees' },
];
const PANEL_ID = 'project-dialog-panel';

/**
 * The **Project dialog**: one project's facts and paths on Overview, and its worktree table on
 * Worktrees. The location's `detail` opens it, and `dtab` picks the tab.
 */
export function ProjectDetail({ projectId }: { projectId: ProjectId }) {
  const { world, status } = useWorld();
  const { tab, showTab, close } = useProjectDetail();
  const newWork = useNewWork();
  const setUp = useSetUpProject();
  const home = useSystem().data?.home;
  const row = useMemo(
    () => listProjects(world).find((p) => p.id === projectId),
    [world, projectId],
  );
  const paths = useMemo(() => projectPaths(world, projectId, home), [world, projectId, home]);
  const firstTask = useMemo(() => !hasUserTasks(world, projectId), [world, projectId]);

  if (!row) {
    return (
      <Dialog label={projectId} onClose={close} testId="project-detail">
        <p>{status === 'ready' ? `No project ${projectId}` : 'Loading…'}</p>
        <DialogFooter>
          <AppButton onClick={close}>Close</AppButton>
        </DialogFooter>
      </Dialog>
    );
  }

  return (
    <Dialog label={row.name} onClose={close} testId="project-detail" className={styles.box}>
      <DialogHeader title={row.name} onClose={close} />
      <div className={styles.tabs} role="tablist" aria-label="Project">
        {TABS.map((t, i) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            className={`${styles.tab} nowrap`}
            aria-selected={t.id === tab}
            aria-controls={PANEL_ID}
            tabIndex={t.id === tab ? 0 : -1}
            onClick={() => showTab(t.id)}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
              const next =
                TABS[(i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length]!;
              showTab(next.id);
              (e.currentTarget.parentElement?.children[TABS.indexOf(next)] as HTMLElement)?.focus();
            }}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.body} role="tabpanel" id={PANEL_ID}>
        {tab === 'worktrees' ? (
          <WorktreeTable project={row.id} />
        ) : (
          <dl className={styles.facts}>
            <dt>main</dt>
            <dd className={paths ? styles.mono : styles.faint} data-testid="project-main-path">
              {paths?.main ?? '…'}
            </dd>
            <dt>worktrees</dt>
            <dd>
              {paths && (
                <span className={styles.mono} data-testid="project-worktree-paths">
                  {paths.worktrees}
                </span>
              )}
              <span className={styles.faint}>{` ${row.openWorktrees} open`}</span>
            </dd>
            <dt>stack tip</dt>
            <dd className={row.stackTip ? styles.mono : styles.faint}>{row.stackTip || 'none'}</dd>
            <dt>linear</dt>
            <dd className={row.hasLinear ? undefined : styles.faint}>
              {row.hasLinear ? 'Linear' : 'none'}
            </dd>
          </dl>
        )}
      </div>
      {setUp.error && (
        <p className={styles.error} role="alert">
          {setUp.error.message}
        </p>
      )}
      <DialogFooter>
        <AppButton
          icon={actionIcon('launch')}
          disabled={setUp.isPending}
          onClick={() =>
            setUp
              .setUp(row.id)
              .then(close)
              .catch(() => {})
          }
        >
          Set up
        </AppButton>
        <AppButton
          variant="primary"
          icon={actionIcon('new')}
          onClick={() =>
            newWork.open({ kind: 'task', project: row.id, title: '', draft: '', comms: [] })
          }
        >
          {firstTask ? 'Create your first task' : 'Create task'}
        </AppButton>
      </DialogFooter>
    </Dialog>
  );
}
