import { useCallback, useMemo } from 'react';
import { useLocation } from 'react-router';
import { currentLoc, useRouter } from './router';
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

/**
 * The history state of an entry this app pushed. Back from such an entry stays in the app; the
 * first entry, a copied link, has none. A link passes it as its `state`.
 */
export const IN_APP = { inApp: true } as const;

const isInApp = (state: unknown) => (state as { inApp?: unknown } | null)?.inApp === true;

/** Options for one move. `replace` swaps the current history entry rather than pushing one. */
export interface GoOptions {
  replace?: boolean;
}

/**
 * A function that moves to the current location with a patch laid over it. It reads the
 * location when it runs, not when the component rendered, so it is safe after an await.
 */
export function useGo(): (patch: LocPatch, opts?: GoOptions) => void {
  const router = useRouter();
  return useCallback(
    (patch, opts) => {
      // A replace keeps the entry what it was: the first entry stays the first.
      const state = opts?.replace ? router.state.location.state : IN_APP;
      void router.navigate(toHref(withLoc(currentLoc(router), patch)), {
        replace: opts?.replace,
        state,
      });
    },
    [router],
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
 * Back, as the narrow layout's Back button does it. From an entry this app pushed it is the
 * browser's Back. On the first entry — a copied link — there is nothing of the app behind,
 * so it replaces the location with its parent rather than leave the app.
 */
export function useBack(): () => void {
  const router = useRouter();
  return useCallback(() => {
    if (isInApp(router.state.location.state)) {
      void router.navigate(-1);
      return;
    }
    const loc = currentLoc(router);
    const parent = parentOf(loc);
    if (parent) void router.navigate(toHref(withLoc(loc, parent)), { replace: true });
  }, [router]);
}
