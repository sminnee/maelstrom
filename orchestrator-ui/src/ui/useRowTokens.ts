import { useEffect, useMemo, useState } from 'react';
import type { DiffRowData } from './DiffRow';
import { highlightLines, languageFor, type Token } from './highlight';

/** Past these, a hunk draws plain: the tokeniser's cost grows with them and a minified line gains nothing. */
const MAX_ROWS = 2000;
const MAX_LINE = 1000;

type Side = 'old' | 'new';

const CACHE_SIZE = 500;
/** Tokens made already, by hunk content, so a refetch or a remount draws coloured at once. */
const cache = new Map<string, Token[][]>();

function cached(key: string): Token[][] | undefined {
  const tokens = cache.get(key);
  if (tokens) {
    // Least recently used goes first: a hit moves to the end.
    cache.delete(key);
    cache.set(key, tokens);
  }
  return tokens;
}

function remember(key: string, tokens: Token[][]) {
  cache.set(key, tokens);
  if (cache.size > CACHE_SIZE) cache.delete(cache.keys().next().value!);
}

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
 * Tokens already made come back at once; new ones are made only while
 * `enabled`, which the Changes tab ties to the hunk being near the screen.
 */
export function useRowTokens(
  rows: DiffRowData[],
  path: string,
  enabled = true,
  shebang?: string | null,
): Token[][] | null {
  const lang = languageFor(path, shebang);
  // The rows' content, not their identity: it is the cache key, and the same
  // text comes back in new rows after a refetch or a remount.
  const key = useMemo(() => {
    const skip = !lang || rows.length > MAX_ROWS || rows.some((row) => row.text.length > MAX_LINE);
    return skip ? null : `${lang}\n${rows.map((r) => r.kind[0] + r.text).join('\n')}`;
  }, [rows, lang]);
  const [done, setDone] = useState<{ key: string; tokens: Token[][] } | null>(null);
  const hit = key === null ? undefined : cached(key);

  useEffect(() => {
    if (key === null || !lang || !enabled || cache.has(key)) return;
    let live = true;
    const sides = sideLines(rows);
    Promise.all([highlightLines(sides.old, lang), highlightLines(sides.new, lang)]).then(
      ([old, next]) => {
        if (!live) return;
        const tokens = sides.index.map(([side, i]) => (side === 'old' ? old : next)[i] ?? []);
        remember(key, tokens);
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
  }, [key, enabled]);

  return hit ?? (done && done.key === key ? done.tokens : null);
}
