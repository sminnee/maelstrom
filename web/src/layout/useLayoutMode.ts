import { useSyncExternalStore } from 'react';

/**
 * Which of the two layouts the app draws. `wide` is the main-monitor tool:
 * the canvas and the panel side by side. `narrow` is the deck list, one
 * screen at a time.
 */
export type LayoutMode = 'narrow' | 'wide';

/**
 * The widest viewport that still reads as narrow. The canvas needs room for a
 * 220px node, a 440px card beside it and a 320px panel; below that the board
 * is a sliver rather than a board, so the break sits above the sum.
 */
const NARROW_MAX = 839;

/** The query the hook watches, and the one place the breakpoint is applied. */
export const NARROW_QUERY = `(max-width: ${NARROW_MAX}px)`;

/** No matchMedia (jsdom, an old browser) reads as wide: the desktop layout is the default. */
const NO_MEDIA = { matches: false, addEventListener() {}, removeEventListener() {} };

function query(): Pick<MediaQueryList, 'matches' | 'addEventListener' | 'removeEventListener'> {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return NO_MEDIA;
  return window.matchMedia(NARROW_QUERY);
}

/**
 * Which layout to draw, following the viewport as it changes.
 *
 * The decision is read here, in TypeScript, rather than only in a media query,
 * so the layout is a component decision the app-boundary suite can assert.
 * `vite.config.ts` sets `css: false`, so a media query is invisible to a test;
 * the CSS carries only cosmetic sizing.
 */
export function useLayoutMode(): LayoutMode {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}

// Stable identities: an inline arrow here would re-subscribe on every render
// of every consumer, and a panel link renders once per card footer entry.
function subscribe(onChange: () => void): () => void {
  const media = query();
  media.addEventListener('change', onChange);
  return () => media.removeEventListener('change', onChange);
}

const snapshot = (): LayoutMode => (query().matches ? 'narrow' : 'wide');
// The server has no viewport, and the desktop layout is the default.
const serverSnapshot = (): LayoutMode => 'wide';
