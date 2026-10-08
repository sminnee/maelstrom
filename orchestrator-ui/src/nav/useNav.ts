import { useCallback, useMemo } from 'react';
import { useLocation, useMatches } from 'react-router';
import { currentLoc, useRouter } from './router';
import { defaultLoc, isScreen, locAt, toHref, withLoc, type Loc, type LocPatch } from './location';

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

/** Where the user is: the screen the routes matched, its params, and the search. */
export function useLoc(): Loc {
  const match = useMatches().at(-1);
  const { search } = useLocation();
  return useMemo(() => {
    const handle: unknown = match?.handle;
    // The routes redirect a path with no screen, so the default is never drawn.
    return share(match && isScreen(handle) ? locAt(handle, match.params, search) : defaultLoc());
  }, [match, search]);
}

/**
 * The history state of an entry this app pushed: the href it was pushed from. The first entry,
 * a copied link, has none. See `useBack` and `GoOptions.close`.
 */
interface PushedState {
  from: string;
}

const pushedFrom = (state: unknown): string | null => {
  const from = (state as Partial<PushedState> | null)?.from;
  return typeof from === 'string' ? from : null;
};

/** The history state for a link: it is pushed from the location it is drawn at. */
export function useLinkState(): PushedState {
  return { from: toHref(useLoc()) };
}

/** Options for one move. */
export interface GoOptions {
  /** Swap the current history entry rather than push one. */
  replace?: boolean;
  /**
   * Close what the current entry opened. When the entry was pushed from the target, this goes
   * back to it, so a later Back does not reopen what was closed. Otherwise it replaces.
   */
  close?: boolean;
}

/**
 * A function that moves to the current location with a patch laid over it. It reads the
 * location when it runs, not when the component rendered, so it is safe after an await.
 */
export function useGo(): (patch: LocPatch, opts?: GoOptions) => void {
  const router = useRouter();
  return useCallback(
    (patch, opts) => {
      const here = currentLoc(router);
      const to = toHref(withLoc(here, patch));
      const { state } = router.state.location;
      if (opts?.close && pushedFrom(state) === to) {
        void router.navigate(-1);
        return;
      }
      const replace = opts?.replace || opts?.close;
      // A replace keeps the entry what it was: the first entry stays the first.
      void router.navigate(to, { replace, state: replace ? state : { from: toHref(here) } });
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
    if (pushedFrom(router.state.location.state) !== null) {
      void router.navigate(-1);
      return;
    }
    const loc = currentLoc(router);
    const parent = parentOf(loc);
    if (parent) void router.navigate(toHref(withLoc(loc, parent)), { replace: true });
  }, [router]);
}
