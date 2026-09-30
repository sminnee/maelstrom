import type { ReactNode } from 'react';
import styles from './DiffRow.module.css';

export type DiffRowKind = 'context' | 'add' | 'remove';

const SIGN: Record<DiffRowKind, string> = { add: '+', remove: '-', context: ' ' };

/**
 * One line of a diff: its sign and its text, on the add, remove or context
 * ground. An Edit card draws it bare; the Changes tab passes the old and new
 * line numbers, which draw as a gutter.
 */
export function DiffRow({
  kind,
  text,
  lineNumbers,
}: {
  kind: DiffRowKind;
  text: string;
  lineNumbers?: { old: number | null; new: number | null };
}) {
  return (
    <div className={styles.row} data-kind={kind} data-testid="diff-row">
      {lineNumbers && (
        <>
          <span className={styles.number}>{lineNumbers.old ?? ''}</span>
          <span className={styles.number}>{lineNumbers.new ?? ''}</span>
        </>
      )}
      <span className={styles.sign}>{SIGN[kind]}</span>
      <span>{text}</span>
    </div>
  );
}

/** The mono block diff rows sit in. It scrolls sideways, so a long line keeps its shape. */
export function DiffBlock({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={[styles.block, className].filter(Boolean).join(' ')}>{children}</div>;
}
