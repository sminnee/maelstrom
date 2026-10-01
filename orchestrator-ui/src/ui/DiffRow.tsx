import { Fragment, type ReactNode } from 'react';
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

/** What makes a row's line numbers a button that selects the row. */
export interface GutterHandlers {
  onPointerDown: (e: React.PointerEvent) => void;
  /** On the whole row, so a drag extends over the text as well as the numbers. */
  onPointerOver: () => void;
  onClick: (e: React.MouseEvent) => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
}

export interface Gutter {
  selected: boolean;
  handlers: GutterHandlers;
}

/**
 * One line of a diff: its sign and its text, on the add, remove or context
 * ground. An Edit card draws it bare; the Changes tab passes the old and new
 * line numbers, which draw as a gutter. With `tokens`, the text draws in
 * syntax colour and only the sign keeps the add or remove hue. With `gutter`,
 * the numbers are a button and the row can draw as selected.
 */
export function DiffRow({
  kind,
  text,
  lineNumbers,
  tokens,
  gutter,
}: DiffRowData & { tokens?: Token[]; gutter?: Gutter }) {
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
      data-selected={gutter?.selected ? 'true' : undefined}
      data-testid="diff-row"
      onPointerOver={gutter?.handlers.onPointerOver}
    >
      {gutter && lineNumbers ? (
        <button
          type="button"
          className={styles.gutter}
          aria-label={
            lineNumbers.new !== null ? `Line ${lineNumbers.new}` : `Old line ${lineNumbers.old}`
          }
          aria-pressed={gutter.selected}
          onPointerDown={gutter.handlers.onPointerDown}
          onClick={gutter.handlers.onClick}
          onKeyDown={gutter.handlers.onKeyDown}
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
}

/**
 * Diff rows of one file, in syntax colour once its language's tokens are ready.
 * `gutter` makes row `i`'s numbers a button, and `after` draws a node below it.
 * An Edit card passes neither.
 */
export function HighlightedRows({
  rows,
  path,
  gutter,
  after,
}: {
  rows: DiffRowData[];
  path: string;
  gutter?: (i: number) => Gutter;
  after?: (i: number) => ReactNode;
}) {
  const tokens = useRowTokens(rows, path);
  return rows.map((row, i) => (
    <Fragment key={i}>
      <DiffRow
        kind={row.kind}
        text={row.text}
        lineNumbers={row.lineNumbers}
        tokens={tokens?.[i]}
        gutter={gutter?.(i)}
      />
      {after?.(i)}
    </Fragment>
  ));
}

/** The mono block diff rows sit in. It scrolls sideways, so a long line keeps its shape. */
export function DiffBlock({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={[styles.block, className].filter(Boolean).join(' ')}>{children}</div>;
}
