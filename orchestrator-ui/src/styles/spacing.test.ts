import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The spacing gate. A gap, a padding or a margin is a token of the chrome's
 * scale, a prose-grid token, or one of the values below. See DESIGN.md § Layout.
 */
const SRC = join(__dirname, '..');

/** A hairline, and its negative for a border that overlaps one. */
const ALLOWED = new Set(['1px', '-1px']);

/**
 * The documented cases, by file. Each literal is allowed in that file alone.
 * Add a line here only with the reason beside it.
 */
const EXCEPTIONS: Record<string, string[]> = {
  // The 11px code-block padding puts the block's text on the prose grid: see
  // the comment at the rule, and DESIGN.md § Rhythm.
  'markdown/Markdown.module.css': ['11px'],
};

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
function offences(file: string, css: string): string[] {
  const allowed = new Set([...ALLOWED, ...(EXCEPTIONS[file] ?? [])]);
  return withoutComments(css)
    .split('\n')
    .flatMap((line, i) => {
      const found: string[] = [];
      // tokens.css declares the scale, so the names are legal there only as a
      // declaration, and none is left.
      if (/--touch\b|--space-\d/.test(line)) found.push(line.trim());
      const value = SPACING.exec(line)?.[1];
      const literals = value?.match(/-?\d*\.?\d+px/g) ?? [];
      if (literals.some((px) => !allowed.has(px))) found.push(line.trim());
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
    expect(offences('x.css', css)).toEqual([
      '2: padding: 2px var(--u);',
      '3: gap: var(--space-2);',
      '4: min-height: var(--touch);',
    ]);
  });

  it('allows a documented literal in its own file only', () => {
    const css = '  padding: 11px var(--u-2);';
    expect(offences('markdown/Markdown.module.css', css)).toEqual([]);
    expect(offences('ui/Other.module.css', css)).toHaveLength(1);
  });

  it.each(cssFiles(SRC).map((path) => [relative(SRC, path), path] as const))(
    '%s is on the scale',
    (file, path) => {
      expect(offences(file, readFileSync(path, 'utf8'))).toEqual([]);
    },
  );
});
