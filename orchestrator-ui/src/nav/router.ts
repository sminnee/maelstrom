import { createContext, useContext } from 'react';
import type { createBrowserRouter } from 'react-router';
import { defaultLoc, parseLocation, type Loc } from './location';

/** The router the app runs on. A test passes a memory router, to read and move its history. */
export type AppRouter = ReturnType<typeof createBrowserRouter>;

/** The router itself, for a move that must read the location as it is now: see `useGo`. */
export const RouterContext = createContext<AppRouter | null>(null);

export function useRouter(): AppRouter {
  const router = useContext(RouterContext);
  if (!router) throw new Error('No router: render under NavRouter');
  return router;
}

/** Where the user is now, as `router` holds it: its own path, less the base it is served under. */
export function currentLoc(router: AppRouter): Loc {
  const { pathname, search } = router.state.location;
  const base = router.basename && router.basename !== '/' ? router.basename : '';
  const path = base && pathname.startsWith(base) ? pathname.slice(base.length) || '/' : pathname;
  return parseLocation(path, search) ?? defaultLoc();
}
