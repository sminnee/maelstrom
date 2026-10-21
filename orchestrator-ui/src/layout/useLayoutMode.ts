import { useSyncExternalStore } from 'react';

/**
 * Which of the three layouts the app draws. `wide` is the main-monitor tool:
 * two slots side by side. `medium` has one slot, so one pane at a time.
 * `narrow` is the deck list, one screen at a time.
 */
export type LayoutMode = 'narrow' | 'medium' | 'wide';

/**
 * The widest viewport that still reads as narrow. The canvas needs room for a
 * 220px node and a 440px card beside it; below 840px the board is a sliver
 * rather than a board.
 */
const NARROW_MAX = 839;

/** The widest viewport that still reads as medium. `orchestrator-ui/DESIGN.md` says why. */
const MEDIUM_MAX = 1599;

/** The queries the hook watches, and the one place each breakpoint is applied. */
const NARROW_QUERY = `(max-width: ${NARROW_MAX}px)`;
const MEDIUM_QUERY = `(max-width: ${MEDIUM_MAX}px)`;

/** No matchMedia (jsdom, an old browser) reads as wide: the desktop layout is the default. */
const NO_MEDIA = { matches: false, addEventListener() {}, removeEventListener() {} };

function query(
  text: string,
): Pick<MediaQueryList, 'matches' | 'addEventListener' | 'removeEventListener'> {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return NO_MEDIA;
  return window.matchMedia(text);
}

/**
 * Which layout to draw, following the viewport as it changes.
 *
 * The decision is read here, in TypeScript, rather than only in a media query,
 * so the layout is a component decision the app-boundary suite can assert.
 * `vite.config.ts` sets `css: false`, so a media query is invisible to a test;
 * the CSS carries only cosmetic sizing.
 *
 * Every caller reads one shared mode. The browser dispatches `change` to each
 * `MediaQueryList` separately and React renders between them, so per-caller
 * lists would let a child draw one layout inside its parent's other one.
 */
export function useLayoutMode(): LayoutMode {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}

type Media = ReturnType<typeof query>;
/** The narrow query, then the medium one. */
type Medias = readonly [Media, Media];

const medias = (): Medias => [query(NARROW_QUERY), query(MEDIUM_QUERY)];
const read = ([narrow, medium]: Medias): LayoutMode =>
  narrow.matches ? 'narrow' : medium.matches ? 'medium' : 'wide';

/** The React listeners, and the one viewport subscription they share while any is held. */
const listeners = new Set<() => void>();
let watching: { medias: Medias; mode: LayoutMode } | null = null;

function onViewportChange() {
  if (!watching) return;
  const mode = read(watching.medias);
  if (mode === watching.mode) return;
  watching.mode = mode;
  // One loop in one task, so React renders every reader together.
  for (const listener of listeners) listener();
}

// Stable identities: an inline arrow here would re-subscribe on every render
// of every consumer, and a panel link renders once per card footer entry.
function subscribe(onChange: () => void): () => void {
  if (!watching) {
    const watched = medias();
    for (const media of watched) media.addEventListener('change', onViewportChange);
    watching = { medias: watched, mode: read(watched) };
  }
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
    if (listeners.size > 0 || !watching) return;
    for (const media of watching.medias) media.removeEventListener('change', onViewportChange);
    watching = null;
  };
}

// Fresh when no one is subscribed yet: the first render reads before any effect runs.
const snapshot = (): LayoutMode => (watching ? watching.mode : read(medias()));
// The server has no viewport, and the desktop layout is the default.
const serverSnapshot = (): LayoutMode => 'wide';
