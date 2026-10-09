import type { TabAttribution } from '../selectors/tabs';
import styles from './TabChip.module.css';

/** A tab's identity: the task's bare id, or a free agent's own id. */
export function TabChip({ attribution }: { attribution: TabAttribution }) {
  return (
    <span className={`${styles.chip} nowrap`} data-testid="tab-chip">
      {attribution.id}
    </span>
  );
}
