import type { TabAttribution } from '../selectors/tabs';
import styles from './TabChip.module.css';

/** A tab's identity: the qualified task id, or a free agent's own id. */
export function TabChip({ attribution }: { attribution: TabAttribution }) {
  return (
    <span className={styles.chip} data-testid="tab-chip">
      {attribution.id}
    </span>
  );
}
