import { readFileSync } from 'node:fs';
import { relative } from 'node:path';
import { describe, expect, it } from 'vitest';
import { lineOffences, SRC, sourceFiles } from '../test/sourceGate';

/**
 * The spacing gate. A gap, a padding or a margin is a token of the chrome's
 * scale, a prose-grid token, or a hairline. See DESIGN.md § Layout.
 */

/** A hairline, and its negative for a border that overlaps one. */
const ALLOWED = new Set(['1px', '-1px']);

const SPACING = /^\s*(?:padding|margin|gap|row-gap|column-gap)(?:-[a-z-]+)?\s*:\s*([^;]*);/;

function offends(line: string): boolean {
  // tokens.css declares the scale, so the names are legal there only as a
  // declaration, and none is left.
  if (/--touch\b|--space-\d/.test(line)) return true;
  const literals = SPACING.exec(line)?.[1]?.match(/-?\d*\.?\d+px/g) ?? [];
  return literals.some((px) => !ALLOWED.has(px));
}

describe('the spacing gate', () => {
  it('finds a px literal, an old token and a touch size, and passes a token', () => {
    const css = [
      '.a { }',
      '  padding: 2px var(--u);',
      '  gap: var(--space-2);',
      '  min-height: var(--touch);',
      '  margin: 0 -1px;',
      '  /* padding: 6px; */',
      '  gap: var(--u-half);',
    ].join('\n');
    expect(lineOffences(css, offends)).toEqual([
      '2: padding: 2px var(--u);',
      '3: gap: var(--space-2);',
      '4: min-height: var(--touch);',
    ]);
  });

  it.each(sourceFiles(SRC, ['.css']).map((path) => [relative(SRC, path), path] as const))(
    '%s is on the scale',
    (_file, path) => {
      expect(lineOffences(readFileSync(path, 'utf8'), offends)).toEqual([]);
    },
  );
});
