import { Fragment, useMemo } from 'react';
import { editToDiffRows } from '../session/diffRows';
import { DiffBlock, type DiffRowData, HighlightedRows } from '../ui/DiffRow';
import type { Mode, RenderedProps } from './plugin';
import styles from './rendered.module.css';

/** Lines of context kept around each change in Changes only, as git keeps. */
const CONTEXT = 3;

/** The default renderer: the file as lines, in syntax colour. */
export function TextView({ old, new: next, mode, path }: RenderedProps) {
  const runs = useMemo(() => textRuns(old, next, mode), [old, next, mode]);
  const shebang = useMemo(() => firstLine(next ?? old), [next, old]);
  if (runs.length === 0)
    return <p className={styles.note}>{mode === 'final' ? 'Deleted file.' : 'No changes.'}</p>;
  return (
    <DiffBlock className={styles.text}>
      {runs.map((rows, i) => (
        <Fragment key={i}>
          {i > 0 && <div className={styles.gap} aria-hidden="true" />}
          <HighlightedRows rows={rows} path={path} shebang={shebang} />
        </Fragment>
      ))}
    </DiffBlock>
  );
}

/** The text's first line, which can name a script's language. */
function firstLine(text: string | null): string | null {
  if (text === null) return null;
  const end = text.indexOf('\n');
  return end < 0 ? text : text.slice(0, end);
}

/** The rows to draw, as runs that a gap parts. */
function textRuns(old: string | null, next: string | null, mode: Mode): DiffRowData[][] {
  if (mode === 'final') {
    if (next === null) return [];
    // One file, so one column of numbers: the old one stays blank.
    return [
      numbered(editToDiffRows(next, next)).map((row) => ({
        ...row,
        lineNumbers: { old: null, new: row.lineNumbers!.new },
      })),
    ];
  }
  const rows = numbered(editToDiffRows(old ?? '', next ?? ''));
  if (mode === 'whole') return [rows];
  // Changes only: each changed row and its context, with runs that touch joined.
  const keep = rows.map(() => false);
  rows.forEach((row, i) => {
    if (row.kind === 'context') return;
    for (let k = Math.max(0, i - CONTEXT); k <= Math.min(rows.length - 1, i + CONTEXT); k++)
      keep[k] = true;
  });
  const runs: DiffRowData[][] = [];
  rows.forEach((row, i) => {
    if (!keep[i]) return;
    if (i === 0 || !keep[i - 1]) runs.push([]);
    runs[runs.length - 1]!.push(row);
  });
  return runs;
}

/** Each row with its old and new line numbers, counted down the file. */
function numbered(rows: DiffRowData[]): DiffRowData[] {
  let oldLine = 1;
  let newLine = 1;
  return rows.map((row) => ({
    ...row,
    lineNumbers: {
      old: row.kind === 'add' ? null : oldLine++,
      new: row.kind === 'remove' ? null : newLine++,
    },
  }));
}
