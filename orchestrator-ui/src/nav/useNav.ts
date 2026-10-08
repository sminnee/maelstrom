import { useCallback, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { defaultLoc, parseLocation, toHref, withLoc, type Loc, type LocPatch } from './location';

/** Parts of locations read so far, by value, least recently read first. */
const interned = new Map<string, unknown>();
const INTERN_LIMIT = 200;

/** The one object held for `value`'s JSON, so equal parts of two locations are one object. */
function intern<T>(value: T): T {
  const key = JSON.stringify(value);
  const held = interned.has(key) ? (interned.get(key) as T) : value;
  // Read again, so it moves to the back: the parts in use are never the ones dropped.
  interned.delete(key);
  interned.set(key, held);
  if (interned.size > INTERN_LIMIT) interned.delete(interned.keys().next().value!);
  return held;
}

/**
 * `loc` with each part interned. A reader memoises on a part, and the canvas fits its view
 * when the filters change: a new panel must not look like new filters.
 */
function share(loc: Loc): Loc {
  const out = { ...loc };
  for (const key of Object.keys(loc) as (keyof Loc)[]) {
    (out as Record<keyof Loc, unknown>)[key] = intern(loc[key]);
  }
  return out;
}

/** Where the user is, read from the URL. */
export function useLoc(): Loc {
  const { pathname, search } = useLocation();
  // The routes redirect a path with no screen, so the default is never drawn for long.
  return useMemo(() => share(parseLocation(pathname, search) ?? defaultLoc()), [pathname, search]);
}

/** Options for one move. `replace` swaps the current history entry rather than pushing one. */
export interface GoOptions {
  replace?: boolean;
}

/** A function that moves to the current location with a patch laid over it. */
export function useGo(): (patch: LocPatch, opts?: GoOptions) => void {
  const loc = useLoc();
  const navigate = useNavigate();
  return useCallback(
    (patch, opts) => {
      void navigate(toHref(withLoc(loc, patch)), { replace: opts?.replace });
    },
    [loc, navigate],
  );
}

/** The href of the current location with a patch laid over it: for a link's `to`. */
export function useHrefFor(patch: LocPatch): string {
  return toHref(withLoc(useLoc(), patch));
}

/**
 * The parent of a location: the screen under the top one. The panel sits over the card, and
 * the card over the view.
 */
export function parentOf(loc: Loc): LocPatch | null {
  // The narrow layout draws no screen for a dev env tab, so it is no level to go back through.
  if (loc.panel && loc.panel.kind !== 'devenv') return { panel: null };
  if (loc.card) return { card: null };
  return null;
}

/**
 * Back, as the narrow layout's Back button does it. With an entry of this app behind the
 * current one it is the browser's Back. On the first entry — a copied link — there is none,
 * so it replaces the location with its parent rather than leave the app.
 */
export function useBack(): () => void {
  const loc = useLoc();
  const { key } = useLocation();
  const navigate = useNavigate();
  return useCallback(() => {
    // The browser router keeps its index in `history.state`; the memory router a test uses
    // does not, and its first entry has the key `default`.
    const idx = (window.history.state as { idx?: unknown } | null)?.idx;
    const canGoBack = typeof idx === 'number' ? idx > 0 : key !== 'default';
    if (canGoBack) {
      void navigate(-1);
      return;
    }
    const parent = parentOf(loc);
    if (parent) void navigate(toHref(withLoc(loc, parent)), { replace: true });
  }, [loc, key, navigate]);
}
