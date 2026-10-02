import { Fragment, memo, type ReactNode } from 'react';
import styles from './DiffRow.module.css';
import { type DiffRowKind, SIGN } from './diffSign';
import type { Token } from './highlight';
import { useRowTokens } from './useRowTokens';

/** A row to draw. The Changes tab gives each its line numbers; an Edit card does not. */
export interface DiffRowData {
  kind: DiffRowKind;
  text: string;
  lineNumbers?: { old: number | null; new: number | null };
}

/**
 * What makes a row's line numbers a button that selects the row. One object
 * serves every row of a file, so each handler takes the row's index.
 */
export interface RowHandlers {
  onPointerDown: (index: number, e: React.PointerEvent) => void;
  /** On the whole row, so a drag extends over the text as well as the numbers. */
  onPointerOver: (index: number) => void;
  onClick: (index: number, e: React.MouseEvent) => void;
  onKeyDown: (index: number, e: React.KeyboardEvent) => void;
}

/**
 * One line of a diff: its sign and its text, on the add, remove or context
 * ground. An Edit card draws it bare; the Changes tab passes the old and new
 * line numbers, which draw as a gutter. With `tokens`, the text draws in
 * syntax colour and only the sign keeps the add or remove hue. With `handlers`,
 * the numbers are a button and the row can draw as selected.
 *
 * A `memo`: see orchestrator-ui.md, "The Changes tab".
 */
const DiffRow = memo(function DiffRow({
  kind,
  text,
  lineNumbers,
  tokens,
  index,
  selected,
  handlers,
}: DiffRowData & {
  tokens?: Token[];
  /** The row's place in its file, which `handlers` gets. */
  index: number;
  selected: boolean;
  handlers?: RowHandlers;
}) {
  const numbers = lineNumbers && (
    <>
      <span className={styles.number}>{lineNumbers.old ?? ''}</span>
      <span className={styles.number}>{lineNumbers.new ?? ''}</span>
    </>
  );
  return (
    <div
      className={styles.row}
      data-kind={kind}
      data-highlighted={tokens ? 'true' : undefined}
      data-selected={selected ? 'true' : undefined}
      data-testid="diff-row"
      onPointerOver={handlers && (() => handlers.onPointerOver(index))}
    >
      {handlers && lineNumbers ? (
        <button
          type="button"
          className={styles.gutter}
          aria-label={
            lineNumbers.new !== null ? `Line ${lineNumbers.new}` : `Old line ${lineNumbers.old}`
          }
          aria-pressed={selected}
          onPointerDown={(e) => handlers.onPointerDown(index, e)}
          onClick={(e) => handlers.onClick(index, e)}
          onKeyDown={(e) => handlers.onKeyDown(index, e)}
        >
          {numbers}
        </button>
      ) : (
        numbers
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
});

/**
 * Diff rows of one file, in syntax colour once its language's tokens are ready.
 * `rows` can be one hunk of the file: `first` is where it begins in the file's
 * rows, and `selected`, `handlers` and `after` count in the file's rows.
 * `handlers` makes each row's numbers a button, and `after` draws a node below
 * a row. An Edit card passes none of them.
 */
export function HighlightedRows({
  rows,
  path,
  first = 0,
  selected = null,
  handlers,
  after,
}: {
  rows: DiffRowData[];
  path: string;
  first?: number;
  /** The first and last selected row. */
  selected?: [number, number] | null;
  handlers?: RowHandlers;
  after?: (index: number) => ReactNode;
}) {
  const tokens = useRowTokens(rows, path);
  return rows.map((row, i) => {
    const index = first + i;
    return (
      <Fragment key={i}>
        <DiffRow
          kind={row.kind}
          text={row.text}
          lineNumbers={row.lineNumbers}
          tokens={tokens?.[i]}
          index={index}
          selected={selected !== null && index >= selected[0] && index <= selected[1]}
          handlers={handlers}
        />
        {after?.(index)}
      </Fragment>
    );
  });
}

/** The mono block diff rows sit in. It scrolls sideways, so a long line keeps its shape. */
export function DiffBlock({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={[styles.block, className].filter(Boolean).join(' ')}>{children}</div>;
}
