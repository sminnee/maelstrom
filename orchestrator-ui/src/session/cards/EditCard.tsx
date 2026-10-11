import { useMemo } from 'react';
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
  const rows = useMemo(() => editToDiffRows(oldString, newString), [oldString, newString]);
  return (
    <DiffBlock className={styles.diff}>
      <HighlightedRows rows={rows} path={path} />
    </DiffBlock>
  );
}
