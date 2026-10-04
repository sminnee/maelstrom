import { readFileSync } from 'node:fs';
import { relative } from 'node:path';
import { describe, expect, it } from 'vitest';
import { lineOffences, SRC, sourceFiles } from '../test/sourceGate';

/**
 * The font-size gate. A font size outside `tokens.css` is one of the seven type
 * tokens, an `em` value relative to its parent, or `inherit`. A literal does
 * not follow the narrow layout's scale. See DESIGN.md § Hierarchy.
 */
const TOKENS = ['sm', 'ui', 'control', 'md', 'lg', 'xl', 'caption'];

const ALLOWED = new RegExp(
  `^(?:var\\(--text-(?:${TOKENS.join('|')})\\)|\\d*\\.?\\d+em|inherit)(?:\\s*!important)?$`,
);

/** `font-size` in CSS, `fontSize` in an inline style. */
const FONT_SIZE = /(?:^\s*font-size\s*:|\bfontSize\s*:)\s*([^;,}\n]*)/;
/** The shorthand sets a size too, so only `inherit` passes. */
const FONT = /^\s*font\s*:\s*([^;]*);/;

const unquoted = (value: string) => value.trim().replace(/^(['"])(.*)\1$/, '$2');

function offends(line: string): boolean {
  const size = FONT_SIZE.exec(line)?.[1];
  if (size !== undefined && !ALLOWED.test(unquoted(size))) return true;
  const font = FONT.exec(line)?.[1];
  return font !== undefined && font.trim() !== 'inherit';
}

describe('the font-size gate', () => {
  it('finds a literal, a removed token and a sized shorthand, and passes the rest', () => {
    const css = [
      '.a { }',
      '  font-size: 11px;',
      '  font-size: var(--text-xs);',
      '  font-size: 1rem;',
      '  /* font-size: 9px; */',
      '  font-size: var(--text-sm);',
      '  font-size: var(--text-md) !important;',
      '  font-size: 0.92em;',
      '  font-size: inherit;',
      '  font: 11px/1.4 sans-serif;',
      '  font: inherit;',
    ].join('\n');
    expect(lineOffences(css, offends)).toEqual([
      '2: font-size: 11px;',
      '3: font-size: var(--text-xs);',
      '4: font-size: 1rem;',
      '10: font: 11px/1.4 sans-serif;',
    ]);
  });

  it('reads an inline style the same way', () => {
    const tsx = [
      "<p style={{ fontSize: 12, color: 'red' }} />",
      "<p style={{ fontSize: 'var(--text-2xs)' }} />",
      "<p style={{ fontSize: 'var(--text-sm)', color: 'red' }} />",
    ].join('\n');
    expect(lineOffences(tsx, offends)).toEqual([
      "1: <p style={{ fontSize: 12, color: 'red' }} />",
      "2: <p style={{ fontSize: 'var(--text-2xs)' }} />",
    ]);
  });

  const files = sourceFiles(SRC, ['.css', '.tsx'], ['tokens.css']);
  it.each(files.map((path) => [relative(SRC, path), path] as const))(
    '%s is on the type scale',
    (_file, path) => {
      expect(lineOffences(readFileSync(path, 'utf8'), offends)).toEqual([]);
    },
  );
});
