import { useEffect, useState } from 'react';
import type { DiffRowData } from './DiffRow';
import { highlightLines, languageFor, type Token } from './highlight';

/** Past these, a hunk draws plain: the tokeniser's cost grows with them and a minified line gains nothing. */
const MAX_ROWS = 2000;
const MAX_LINE = 1000;

type Side = 'old' | 'new';

/**
 * Splits rows into the old side (context and remove rows) and the new side
 * (context and add rows). `index` gives each row its side and its line on
 * that side.
 */
function sideLines(rows: DiffRowData[]) {
  const old: string[] = [];
  const next: string[] = [];
  const index: [Side, number][] = [];
  for (const { kind, text } of rows) {
    if (kind !== 'add') old.push(text);
    if (kind !== 'remove') next.push(text);
    index.push(kind === 'remove' ? ['old', old.length - 1] : ['new', next.length - 1]);
  }
  return { old, new: next, index };
}

/**
 * Each row's tokens, or null until they are ready and for a file with no
 * language. A hunk interleaves two files, so each side is highlighted alone.
 */
export function useRowTokens(rows: DiffRowData[], path: string): Token[][] | null {
  const lang = languageFor(path);
  const skip = !lang || rows.length > MAX_ROWS || rows.some((row) => row.text.length > MAX_LINE);
  // The rows' content, not their identity: an Edit card rebuilds its rows each render.
  const key = skip ? null : `${lang}\n${rows.map((r) => r.kind[0] + r.text).join('\n')}`;
  const [done, setDone] = useState<{ key: string; tokens: Token[][] } | null>(null);

  useEffect(() => {
    if (key === null || !lang) return;
    let live = true;
    const sides = sideLines(rows);
    Promise.all([highlightLines(sides.old, lang), highlightLines(sides.new, lang)]).then(
      ([old, next]) => {
        if (!live) return;
        const tokens = sides.index.map(([side, i]) => (side === 'old' ? old : next)[i] ?? []);
        setDone({ key, tokens });
      },
      // The rows stay plain; the next diff tries the load again.
      (error: unknown) => console.warn('Syntax highlighting failed', error),
    );
    return () => {
      live = false;
    };
    // `key` stands for `rows` and `lang`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return done && done.key === key ? done.tokens : null;
}
