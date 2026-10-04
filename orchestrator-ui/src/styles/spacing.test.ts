import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The spacing gate. A gap, a padding or a margin is a token of the chrome's
 * scale, a prose-grid token, or a hairline. See DESIGN.md § Layout.
 */
const SRC = join(__dirname, '..');

/** A hairline, and its negative for a border that overlaps one. */
const ALLOWED = new Set(['1px', '-1px']);

const SPACING = /^\s*(?:padding|margin|gap|row-gap|column-gap)(?:-[a-z-]+)?\s*:\s*([^;]*);/;

function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return cssFiles(path);
    return entry.name.endsWith('.css') ? [path] : [];
  });
}

/** The source with each comment blanked, so prose about `12px` is not a finding. */
const withoutComments = (css: string) =>
  css.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '));

/** Each offence of a file, as `line: text`. */
function offences(css: string): string[] {
  return withoutComments(css)
    .split('\n')
    .flatMap((line, i) => {
      const found: string[] = [];
      // tokens.css declares the scale, so the names are legal there only as a
      // declaration, and none is left.
      if (/--touch\b|--space-\d/.test(line)) found.push(line.trim());
      const value = SPACING.exec(line)?.[1];
      const literals = value?.match(/-?\d*\.?\d+px/g) ?? [];
      if (literals.some((px) => !ALLOWED.has(px))) found.push(line.trim());
      return found.map((text) => `${i + 1}: ${text}`);
    });
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
    expect(offences(css)).toEqual([
      '2: padding: 2px var(--u);',
      '3: gap: var(--space-2);',
      '4: min-height: var(--touch);',
    ]);
  });

  it.each(cssFiles(SRC).map((path) => [relative(SRC, path), path] as const))(
    '%s is on the scale',
    (_file, path) => {
      expect(offences(readFileSync(path, 'utf8'))).toEqual([]);
    },
  );
});
