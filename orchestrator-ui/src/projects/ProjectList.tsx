import { useMemo, useState } from 'react';
import { useWorld } from '../api/useWorld';
import { listProjects } from '../selectors/projects';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { AddProject } from './AddProject';
import styles from './ProjectList.module.css';

/** Every project, with its open worktrees, and the way to add one. */
export function ProjectList() {
  const { world, status } = useWorld();
  const [adding, setAdding] = useState(false);
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
            <tr key={row.id} data-project-id={row.id}>
              <td className={`${styles.name} truncate`}>{row.name}</td>
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
    </div>
  );
}
