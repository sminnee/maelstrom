import type { TurnResultItem } from '../../protocol/transcript';
import styles from './cards.module.css';

/**
 * How a turn ended, and how long it took.
 *
 * No cost: `costUsd` on the item is the session's total, not the turn's, so
 * the session header is where it is said. The field stays on the item — the
 * TUI's own result line still reads it.
 */
export function ResultLine({ item }: { item: TurnResultItem }) {
  return (
    <div className={styles.result}>
      turn {item.subtype} · {(item.durationMs / 1000).toFixed(1)}s
    </div>
  );
}
