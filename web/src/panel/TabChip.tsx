import type { TabAttribution } from '../selectors/tabs';
import styles from './TabChip.module.css';

/** A phase swatch plus the task id: every tab carries one so two agents' tabs are told apart. */
export function TabChip({ attribution }: { attribution: TabAttribution }) {
  return (
    <span
      className={styles.chip}
      data-phase={attribution.phase ?? undefined}
      data-testid="tab-chip"
    >
      <span className={styles.swatch} />
      <span className={styles.task}>{attribution.taskId}</span>
    </span>
  );
}
