import type { ReactNode } from 'react';
import { replace, type RouteObject } from 'react-router';
import { AppShell } from '../shell/AppShell';
import { screenRoutes } from './location';

/**
 * One route per screen, under a layout route that draws `layout`. The screens share that
 * route, so a move between them keeps the layout mounted: `useLoc` reads which one matched.
 * Any other path, `/` among them, redirects to the desk in place of its entry, and keeps its
 * search: Back must not land on a path that redirects again.
 */
export function routesFor(layout: ReactNode): RouteObject[] {
  return [
    { element: layout, children: screenRoutes() },
    {
      path: '*',
      loader: ({ request }) => replace(`/desk${new URL(request.url).search}`),
      // The loader runs before the first render: nothing is drawn while it redirects.
      HydrateFallback: () => null,
    },
  ];
}

/** The app's routes: the shell on every screen. */
export const routes = routesFor(<AppShell />);
