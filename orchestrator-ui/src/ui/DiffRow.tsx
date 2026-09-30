import type { ReactNode } from 'react';
import styles from './DiffRow.module.css';
import type { Token } from './highlight';
import { useRowTokens } from './useRowTokens';

export type DiffRowKind = 'context' | 'add' | 'remove';

/** A row to draw. The Changes tab gives each its line numbers; an Edit card does not. */
export interface DiffRowData {
  kind: DiffRowKind;
  text: string;
  lineNumbers?: { old: number | null; new: number | null };
}

const SIGN: Record<DiffRowKind, string> = { add: '+', remove: '-', context: ' ' };

/**
 * One line of a diff: its sign and its text, on the add, remove or context
 * ground. An Edit card draws it bare; the Changes tab passes the old and new
 * line numbers, which draw as a gutter. With `tokens`, the text draws in
 * syntax colour and only the sign keeps the add or remove hue.
 */
export function DiffRow({ kind, text, lineNumbers, tokens }: DiffRowData & { tokens?: Token[] }) {
  return (
    <div
      className={styles.row}
      data-kind={kind}
      data-highlighted={tokens ? 'true' : undefined}
      data-testid="diff-row"
    >
      {lineNumbers && (
        <>
          <span className={styles.number}>{lineNumbers.old ?? ''}</span>
          <span className={styles.number}>{lineNumbers.new ?? ''}</span>
        </>
      )}
      <span className={styles.sign}>{SIGN[kind]}</span>
      <span>
        {tokens
          ? tokens.map((t, i) => (
              <span key={i} style={{ color: t.color }}>
                {t.content}
              </span>
            ))
          : text}
      </span>
    </div>
  );
}

/** Diff rows of one file, in syntax colour once its language's tokens are ready. */
export function HighlightedRows({ rows, path }: { rows: DiffRowData[]; path: string }) {
  const tokens = useRowTokens(rows, path);
  return rows.map((row, i) => (
    <DiffRow
      key={i}
      kind={row.kind}
      text={row.text}
      lineNumbers={row.lineNumbers}
      tokens={tokens?.[i]}
    />
  ));
}

/** The mono block diff rows sit in. It scrolls sideways, so a long line keeps its shape. */
export function DiffBlock({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={[styles.block, className].filter(Boolean).join(' ')}>{children}</div>;
}
