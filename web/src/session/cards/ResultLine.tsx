import type { TurnResultItem } from '../../protocol/transcript';
import styles from './cards.module.css';

export function ResultLine({ item }: { item: TurnResultItem }) {
  return (
    <div className={styles.result}>
      turn {item.subtype} · ${item.costUsd.toFixed(4)} · {(item.durationMs / 1000).toFixed(1)}s
    </div>
  );
}
