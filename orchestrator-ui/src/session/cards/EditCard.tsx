import { DiffBlock, HighlightedRows } from '../../ui/DiffRow';
import { editToDiffRows } from '../diffRows';
import styles from './cards.module.css';

export function EditCard({
  path,
  oldString,
  newString,
}: {
  path: string;
  oldString: string;
  newString: string;
}) {
  return (
    <DiffBlock className={styles.diff}>
      <HighlightedRows rows={editToDiffRows(oldString, newString)} path={path} />
    </DiffBlock>
  );
}
