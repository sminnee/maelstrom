import { useMemo, useState } from 'react';
import { useWorld } from '../api/useWorld';
import { useProjectDetail } from '../nav/useOverlays';
import { listProjects } from '../selectors/projects';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { AddProject } from './AddProject';
import { ProjectDetail } from './ProjectDetail';
import styles from './ProjectList.module.css';

/** Every project, with its open worktrees, and the way to add one. A row opens its detail. */
export function ProjectList() {
  const { world, status } = useWorld();
  const [adding, setAdding] = useState(false);
  const detail = useProjectDetail();
  const rows = useMemo(() => listProjects(world), [world]);

  return (
    <div className={styles.view} data-testid="project-list">
      <div className={styles.toolbar}>
        <AppButton icon={actionIcon('new')} onClick={() => setAdding(true)}>
          Add project
        </AppButton>
      </div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>project</th>
            <th>worktrees</th>
            <th>stack tip</th>
            <th>linear</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            // The name's button is the keyboard way in. Its click bubbles to the row's.
            <tr
              key={row.id}
              data-project-id={row.id}
              className={styles.row}
              onClick={() => detail.open(row.id)}
            >
              <td className={`${styles.name} truncate`}>
                <button type="button" className={styles.open}>
                  {row.name}
                </button>
              </td>
              <td className={`${styles.count} nowrap`}>{`${row.openWorktrees} open`}</td>
              <td className={`${styles.tip} truncate`}>{row.stackTip}</td>
              <td className={row.hasLinear ? undefined : styles.faint} data-linear={row.hasLinear}>
                {row.hasLinear ? 'Linear' : 'none'}
              </td>
            </tr>
          ))}
          {status === 'ready' && rows.length === 0 && (
            <tr>
              <td colSpan={4} className={styles.faint}>
                No projects yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {adding && <AddProject onClose={() => setAdding(false)} />}
      {detail.projectId && <ProjectDetail projectId={detail.projectId} />}
    </div>
  );
}
