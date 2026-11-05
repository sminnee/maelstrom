import { useMemo } from 'react';
import { useWorld } from '../api/useWorld';
import { useProjectDetail } from '../nav/useOverlays';
import type { ProjectId } from '../protocol/ids';
import { listProjects } from '../selectors/projects';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import { useSetUpProject } from './useSetUpProject';
import styles from './ProjectDetail.module.css';

/** One project's facts, and the action that sets it up. The location's `detail` opens it. */
export function ProjectDetail({ projectId }: { projectId: ProjectId }) {
  const { world, status } = useWorld();
  const { close } = useProjectDetail();
  const setUp = useSetUpProject();
  const row = useMemo(
    () => listProjects(world).find((p) => p.id === projectId),
    [world, projectId],
  );

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
      <div className={styles.body}>
        <dl className={styles.facts}>
          <dt>worktrees</dt>
          <dd>{`${row.openWorktrees} open`}</dd>
          <dt>stack tip</dt>
          <dd className={row.stackTip ? styles.mono : styles.faint}>{row.stackTip || 'none'}</dd>
          <dt>linear</dt>
          <dd className={row.hasLinear ? undefined : styles.faint}>
            {row.hasLinear ? 'Linear' : 'none'}
          </dd>
        </dl>
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
      </DialogFooter>
    </Dialog>
  );
}
