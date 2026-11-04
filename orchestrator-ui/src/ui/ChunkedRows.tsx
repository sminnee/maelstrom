import { type CSSProperties, Fragment, type ReactNode, useMemo, useRef } from 'react';
import { type DiffRowData, HighlightedRows, type RowHandlers } from './DiffRow';
import styles from './ChunkedRows.module.css';
import { useInView } from './useInView';

/** Rows per chunk. */
export const CHUNK_ROWS = 200;

/**
 * A node drawn before the row at `before`, or after the last at `rows.length`.
 * `height` is how many rows tall it draws, so an unmounted chunk keeps its size.
 */
export interface RowBreak {
  key: string;
  before: number;
  height: number;
  node: ReactNode;
}

interface Chunk {
  start: number;
  end: number;
  breaks: RowBreak[];
}

/**
 * Diff rows in chunks of {@link CHUNK_ROWS}. A chunk far from the screen draws
 * as an empty box of its rows' height, so a long file mounts only what is
 * near the view. Rows never wrap, so the height is exact. A chunk that holds a
 * row of `hold` stays mounted: an open comment box keeps its text. A break
 * starts a new run of rows, which highlight apart. `selected`, `handlers` and
 * `after` count in all `rows`, as `HighlightedRows` does.
 */
export function ChunkedRows({
  rows,
  breaks,
  path,
  shebang,
  selected,
  handlers,
  after,
  hold,
}: {
  rows: DiffRowData[];
  breaks: RowBreak[];
  path: string;
  shebang: string | null;
  selected: [number, number] | null;
  handlers: RowHandlers;
  after: (index: number) => ReactNode;
  /** The rows whose chunks stay mounted out of view. */
  hold: number[];
}) {
  const chunks: Chunk[] = [];
  for (let start = 0; start < rows.length || chunks.length === 0; start += CHUNK_ROWS) {
    const end = Math.min(start + CHUNK_ROWS, rows.length);
    const last = end === rows.length;
    chunks.push({
      start,
      end,
      breaks: breaks.filter(
        (b) => b.before >= start && (b.before < end || (last && b.before === end)),
      ),
    });
  }
  return chunks.map((chunk, i) => (
    <ChunkRows
      key={chunk.start}
      first={i === 0}
      chunk={chunk}
      rows={rows}
      path={path}
      shebang={shebang}
      selected={selected}
      handlers={handlers}
      after={after}
      held={hold.some((n) => n >= chunk.start && n < chunk.end)}
    />
  ));
}

function ChunkRows({
  first,
  chunk,
  rows,
  path,
  shebang,
  selected,
  handlers,
  after,
  held,
}: {
  first: boolean;
  chunk: Chunk;
  rows: DiffRowData[];
  path: string;
  shebang: string | null;
  selected: [number, number] | null;
  handlers: RowHandlers;
  after: (index: number) => ReactNode;
  held: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { latch: false, initial: first });
  // Each break starts a run of rows, which highlights apart from the one before.
  // The runs keep their identity across draws, so their tokens are not made again.
  const { start, end } = chunk;
  const cutKey = chunk.breaks.map((b) => b.before).join(',');
  const runs = useMemo(() => {
    const cuts = [...new Set([start, ...cutKey.split(',').filter(Boolean).map(Number), end])].sort(
      (a, b) => a - b,
    );
    return cuts.map((from, c) => ({ from, rows: rows.slice(from, cuts[c + 1] ?? end) }));
  }, [rows, start, end, cutKey]);
  // One element whether mounted or not, so the observer keeps watching it.
  if (!inView && !held) {
    const height = end - start + chunk.breaks.reduce((n, b) => n + b.height, 0);
    return (
      <div
        ref={ref}
        className={styles.unmounted}
        style={{ '--rows': height } as CSSProperties}
        data-chunk=""
      />
    );
  }
  return (
    <div ref={ref} data-chunk="">
      {runs.map((run) => {
        return (
          <Fragment key={run.from}>
            {chunk.breaks
              .filter((b) => b.before === run.from)
              .map((b) => (
                <Fragment key={b.key}>{b.node}</Fragment>
              ))}
            {run.rows.length > 0 && (
              <HighlightedRows
                path={path}
                shebang={shebang}
                rows={run.rows}
                first={run.from}
                selected={selected}
                handlers={handlers}
                after={after}
                highlight={inView}
              />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}
