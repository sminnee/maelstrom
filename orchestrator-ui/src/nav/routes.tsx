import type { ReactNode } from 'react';
import { replace, type LoaderFunctionArgs, type RouteObject } from 'react-router';
import { AppShell } from '../shell/AppShell';
import { DEFAULT_DESK_MODE, oldDeskRoutes, screenRoutes } from './location';

/**
 * One route per screen, under a layout route that draws `layout`. The screens share that
 * route, so a move between them keeps the layout mounted: `useLoc` reads which one matched.
 * Any other path, `/` among them, redirects to the desk in place of its entry, and keeps its
 * search: Back must not land on a path that redirects again. `/desk` and its old card paths
 * redirect the same way, to the same card in the default **Desk mode**.
 */
export function routesFor(layout: ReactNode): RouteObject[] {
  return [
    { element: layout, children: screenRoutes() },
    // Built from the params, not the request's path: that path holds the router's basename.
    ...oldDeskRoutes().map(({ path, to }) => ({
      path,
      loader: ({ request, params }: LoaderFunctionArgs) =>
        replace(`${to(params)}${new URL(request.url).search}`),
      HydrateFallback: () => null,
    })),
    {
      path: '*',
      loader: ({ request }) => replace(`/desk/${DEFAULT_DESK_MODE}${new URL(request.url).search}`),
      // The loader runs before the first render: nothing is drawn while it redirects.
      HydrateFallback: () => null,
    },
  ];
}

/** The app's routes: the shell on every screen. */
export const routes = routesFor(<AppShell />);
