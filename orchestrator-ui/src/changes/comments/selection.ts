import type { ChangeComment, FileDiff } from '../../protocol/entities';
import type { DiffRowData } from '../../ui/DiffRow';
import { SIGN } from '../../ui/diffSign';

/** The lines a comment is on: what a selection gives, and what a held comment keeps. */
export type Span = Pick<ChangeComment, 'side' | 'startLine' | 'endLine' | 'lines'>;

/** A comment the user is writing. `id` names the added comment it edits, if any. */
export type OpenComment = Omit<ChangeComment, 'id'> & { id?: string };

/** The held text of one worktree's Changes tab. */
export interface HeldComments {
  comments: ChangeComment[];
  open: OpenComment | null;
}

/** Every row of a file in one list, so a selection can cross a hunk. */
export function fileRows(file: FileDiff): DiffRowData[] {
  return file.hunks.flatMap((hunk) =>
    hunk.lines.map((line) => ({
      kind: line.kind,
      text: line.text,
      lineNumbers: { old: line.oldLine, new: line.newLine },
    })),
  );
}

/** The first and last row between an anchor and a focus, whichever way the drag went. */
export function rangeOf(anchor: number, focus: number): [number, number] {
  return anchor <= focus ? [anchor, focus] : [focus, anchor];
}

const quoted = (row: DiffRowData) => SIGN[row.kind] + row.text;
const numbers = (rows: DiffRowData[], side: Span['side']) =>
  rows.flatMap((row) => row.lineNumbers?.[side] ?? []);

/**
 * The span of rows `from` to `to`. It counts in new line numbers, which are
 * the lines the agent can open. Removed rows alone have none, so they count
 * in old ones.
 */
export function spanOf(rows: DiffRowData[], from: number, to: number): Span | null {
  const picked = rows.slice(from, to + 1);
  const side = numbers(picked, 'new').length > 0 ? 'new' : 'old';
  const lines = numbers(picked, side);
  if (lines.length === 0) return null;
  return { side, startLine: lines[0]!, endLine: lines.at(-1)!, lines: picked.map(quoted) };
}

/**
 * Where `span` is in the rows in view: its first and last row, or `null`
 * when no rows have its numbers and read as its quoted lines.
 */
export function placeOf(rows: DiffRowData[], span: Span): [number, number] | null {
  const count = span.lines.length;
  for (let from = 0; from + count <= rows.length; from++) {
    if (quoted(rows[from]!) !== span.lines[0]) continue;
    const found = spanOf(rows, from, from + count - 1);
    if (
      found &&
      found.side === span.side &&
      found.startLine === span.startLine &&
      found.endLine === span.endLine &&
      found.lines.every((line, i) => line === span.lines[i])
    ) {
      return [from, from + count - 1];
    }
  }
  return null;
}

/** `line 5`, `lines 4-5`, or `old line 5`: the span as the message names it. */
export function spanLabel(span: Span): string {
  const lines =
    span.startLine === span.endLine
      ? `line ${span.startLine}`
      : `lines ${span.startLine}-${span.endLine}`;
  return span.side === 'old' ? `old ${lines}` : lines;
}
