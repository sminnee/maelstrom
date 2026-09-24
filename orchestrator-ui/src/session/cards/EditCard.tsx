import { editToDiffRows } from '../diffRows';
import styles from './cards.module.css';

export function EditCard({ oldString, newString }: { oldString: string; newString: string }) {
  const rows = editToDiffRows(oldString, newString);
  return (
    <div className={styles.diff}>
      {rows.map((row, i) => (
        <div key={i} className={styles.diffRow} data-kind={row.kind} data-testid="diff-row">
          <span className={styles.sign}>
            {row.kind === 'add' ? '+' : row.kind === 'remove' ? '-' : ' '}
          </span>
          <span>{row.text}</span>
        </div>
      ))}
    </div>
  );
}
