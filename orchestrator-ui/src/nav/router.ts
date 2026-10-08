import { createContext, useContext } from 'react';
import type { createBrowserRouter } from 'react-router';
import { defaultLoc, isScreen, locAt, type Loc } from './location';

/** The router the app runs on. A test passes a memory router, to read and move its history. */
export type AppRouter = ReturnType<typeof createBrowserRouter>;

/** The router itself, for a move that must read the location as it is now: see `useGo`. */
export const RouterContext = createContext<AppRouter | null>(null);

export function useRouter(): AppRouter {
  const router = useContext(RouterContext);
  if (!router) throw new Error('No router: render under NavRouter');
  return router;
}

/** Where the user is now, as `router` holds it: the screen its routes matched, and the search. */
export function currentLoc(router: AppRouter): Loc {
  const match = router.state.matches.at(-1);
  const handle: unknown = match?.route.handle;
  return match && isScreen(handle)
    ? locAt(handle, match.params, router.state.location.search)
    : defaultLoc();
}
