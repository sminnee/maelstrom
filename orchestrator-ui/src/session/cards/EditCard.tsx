import { DiffBlock, DiffRow } from '../../ui/DiffRow';
import { editToDiffRows } from '../diffRows';
import styles from './cards.module.css';

export function EditCard({ oldString, newString }: { oldString: string; newString: string }) {
  const rows = editToDiffRows(oldString, newString);
  return (
    <DiffBlock className={styles.diff}>
      {rows.map((row, i) => (
        <DiffRow key={i} kind={row.kind} text={row.text} />
      ))}
    </DiffBlock>
  );
}
