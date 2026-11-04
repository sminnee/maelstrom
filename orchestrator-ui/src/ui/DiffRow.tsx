import { type CSSProperties, Fragment, memo, type ReactNode } from 'react';
import { useLayoutMode } from '../layout/useLayoutMode';
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
 * the numbers are a button and the row can draw as selected. With `oneColumn`,
 * the gutter is one number: the new one, or the old one on a removed row. The
 * sign tells the two apart, and the narrow layout gives the room to the code.
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
  oneColumn = false,
}: DiffRowData & {
  tokens?: Token[];
  /** The row's place in its file, which `handlers` gets. */
  index: number;
  selected: boolean;
  handlers?: RowHandlers;
  oneColumn?: boolean;
}) {
  const numbers =
    lineNumbers &&
    (oneColumn ? (
      <span className={styles.number}>{lineNumbers.new ?? lineNumbers.old ?? ''}</span>
    ) : (
      <>
        <span className={styles.number}>{lineNumbers.old ?? ''}</span>
        <span className={styles.number}>{lineNumbers.new ?? ''}</span>
      </>
    ));
  const sign = <span className={styles.sign}>{SIGN[kind]}</span>;
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
        // The sign is in the button too, so a finger has more to hit.
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
          {sign}
        </button>
      ) : (
        <>
          {numbers}
          {sign}
        </>
      )}
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
 * a row. An Edit card passes none of them. The narrow layout draws one
 * line-number column.
 */
export function HighlightedRows({
  rows,
  path,
  first = 0,
  selected = null,
  handlers,
  after,
  highlight = true,
  shebang,
}: {
  rows: DiffRowData[];
  path: string;
  shebang?: string | null;
  /** False holds back the tokeniser; tokens it made already still show. */
  highlight?: boolean;
  first?: number;
  /** The first and last selected row. */
  selected?: [number, number] | null;
  handlers?: RowHandlers;
  after?: (index: number) => ReactNode;
}) {
  const tokens = useRowTokens(rows, path, highlight, shebang);
  const oneColumn = useLayoutMode() === 'narrow';
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
          oneColumn={oneColumn}
        />
        {after?.(index)}
      </Fragment>
    );
  });
}

/**
 * The mono block diff rows sit in. It scrolls sideways, so a long line keeps
 * its shape. With `rows`, the gutter is as wide as their widest line number.
 */
export function DiffBlock({
  children,
  className,
  rows,
}: {
  children: ReactNode;
  className?: string;
  rows?: DiffRowData[];
}) {
  const style = rows && ({ '--gutter-digits': gutterDigits(rows) } as CSSProperties);
  return (
    <div className={[styles.block, className].filter(Boolean).join(' ')} style={style}>
      {children}
    </div>
  );
}

/** The digits in the widest line number of `rows`. */
function gutterDigits(rows: DiffRowData[]): number {
  let max = 0;
  for (const { lineNumbers } of rows)
    max = Math.max(max, lineNumbers?.old ?? 0, lineNumbers?.new ?? 0);
  return String(max).length;
}
