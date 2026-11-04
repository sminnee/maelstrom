import { readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  MEDIUM_NOT_NARROW_QUERY,
  NARROW_QUERY,
  NOT_NARROW_QUERY,
  useLayoutMode,
} from '../layout/useLayoutMode';
import { setViewport } from '../test/setup';
import { SRC, sourceFiles, withoutComments } from '../test/sourceGate';

/**
 * The media-query gate. A CSS file names the layout by custom media, such as
 * `@media (--narrow)`, and never writes a layout breakpoint itself: a
 * hand-written `(max-width: 839px)` would leave a phone in landscape on the
 * desktop CSS while the hook draws it narrow. `media.css` defines the custom
 * media, with the strings `layout/useLayoutMode.ts` exports.
 */
const MEDIA_CSS = join(SRC, 'styles', 'media.css');

/** Each `@media` prelude, across lines: prettier wraps a long query list. */
const MEDIA = /@media\s+([^{]*?)\s*\{/g;
const BREAKPOINT = /\b(?:539|540|839|840|959|960|1599|1600)px\b/;
const CUSTOM = /\(--([\w-]+)\)/g;

/** Each custom media `media.css` defines, by name. */
function definitions(): Record<string, string> {
  const text = withoutComments(readFileSync(MEDIA_CSS, 'utf8'));
  const found = [...text.matchAll(/@custom-media\s+--([\w-]+)\s+([^;]*);/g)];
  return Object.fromEntries(found.map(([, name, query]) => [name, query!.replace(/\s+/g, ' ')]));
}

/**
 * Each query that writes a breakpoint or names undefined custom media, as
 * `line: query`, with its whitespace collapsed. postcss-custom-media leaves an
 * unknown name in place, and a browser matches it nowhere.
 */
function offences(source: string, defined: string[]): string[] {
  const text = withoutComments(source);
  return [...text.matchAll(MEDIA)].flatMap((match) => {
    const query = match[1]!.replace(/\s+/g, ' ');
    const unknown = [...query.matchAll(CUSTOM)].some(([, name]) => !defined.includes(name!));
    if (!BREAKPOINT.test(query) && !unknown) return [];
    const line = text.slice(0, match.index).split('\n').length;
    return [`${line}: ${query}`];
  });
}

describe('the media-query gate', () => {
  it('finds a written breakpoint, wrapped or not, and unknown custom media, and passes the rest', () => {
    const css = [
      '@media (max-width: 839px) {',
      '@media (min-width: 840px) {',
      '@media (--narrow) {',
      '@media (--narow) {',
      '@media (prefers-reduced-motion: reduce) {',
      '/* @media (max-width: 839px) { */',
      '@media (max-width: 1599px),',
      '  (pointer: coarse) {',
    ].join('\n');
    expect(offences(css, ['narrow'])).toEqual([
      '1: (max-width: 839px)',
      '2: (min-width: 840px)',
      '4: (--narow)',
      '7: (max-width: 1599px), (pointer: coarse)',
    ]);
  });

  it('defines the custom media with the queries the hook reads', () => {
    expect(definitions()).toEqual({
      narrow: NARROW_QUERY,
      'not-narrow': NOT_NARROW_QUERY,
      'medium-not-narrow': MEDIUM_NOT_NARROW_QUERY,
    });
  });

  const defined = Object.keys(definitions());
  const files = sourceFiles(SRC, ['.css'], ['media.css']);
  it.each(files.map((path) => [relative(SRC, path), path] as const))(
    '%s names the layout by custom media',
    (_file, path) => {
      expect(offences(readFileSync(path, 'utf8'), defined)).toEqual([]);
    },
  );
});

/**
 * The CSS queries and the hook name the same layout. A device the hook draws
 * medium, and the CSS reads as neither narrow nor medium, gets the wrong sizes.
 */
describe('the exported queries', () => {
  const DEVICES = [
    { about: 'a phone', width: 390, height: 844, pointer: 'coarse', mode: 'narrow' },
    { about: 'a phone in landscape', width: 932, height: 430, pointer: 'coarse', mode: 'narrow' },
    { about: 'a small tablet', width: 1024, height: 768, pointer: 'coarse', mode: 'medium' },
    {
      about: 'a tablet over its keyboard',
      width: 1280,
      height: 450,
      pointer: 'coarse',
      mode: 'medium',
    },
    { about: 'a short desktop window', width: 932, height: 430, pointer: 'fine', mode: 'medium' },
    {
      about: 'a short screen with no pointer',
      width: 932,
      height: 430,
      pointer: 'none',
      mode: 'medium',
    },
    { about: 'a laptop', width: 1440, height: 900, pointer: 'fine', mode: 'medium' },
    { about: 'a monitor', width: 1920, height: 1080, pointer: 'fine', mode: 'wide' },
  ] as const;

  it.each(DEVICES)('read $about as $mode', ({ width, height, pointer, mode }) => {
    setViewport({ width, height, pointer });
    expect(renderHook(() => useLayoutMode()).result.current).toBe(mode);
    const matches = (query: string) => window.matchMedia(query).matches;
    expect(matches(NARROW_QUERY)).toBe(mode === 'narrow');
    expect(matches(NOT_NARROW_QUERY)).toBe(mode !== 'narrow');
    expect(matches(MEDIUM_NOT_NARROW_QUERY)).toBe(mode === 'medium');
  });
});
