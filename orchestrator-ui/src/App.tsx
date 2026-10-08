import { useState } from 'react';
import { createBrowserRouter } from 'react-router';
import type { QueryClient } from '@tanstack/react-query';
import { ApiProvider } from './api/ApiProvider';
import type { ApiClient } from './api/http';
import { createQueryClient } from './api/queryClient';
import type { EventSourceLike } from './live/changeStream';
import { LiveProvider } from './live/LiveProvider';
import type { SocketLike } from './live/socketLike';
import { NavRouter } from './nav/NavRouter';
import type { AppRouter } from './nav/router';
import { routes } from './nav/routes';
import { eventsUrl } from './eventsUrl';

/** What the app reaches the server through. A test injects fakes for each. */
export interface AppDeps {
  api: ApiClient;
  eventSourceFactory?: (url: string) => EventSourceLike;
  /** Opens a transcript socket on a same-origin path. */
  webSocketFactory?: (path: string) => SocketLike;
  /** The first wait before a transcript socket reconnects. Tests shorten it. */
  streamReconnectMs?: number;
  queryClient?: QueryClient;
  /** What reads and moves the location. A browser router on the window's URL when left out. */
  router?: AppRouter;
}

/** Where the change stream is — see `eventsUrl`. */
export const EVENTS_URL = eventsUrl(window.location, import.meta.env.VITE_ORCHESTRATOR_PORT);

export function App({ deps }: { deps: AppDeps }) {
  // One client for the life of the app: useMemo may recompute, useState never does.
  const [queryClient] = useState(() => deps.queryClient ?? createQueryClient());
  const [router] = useState(() => deps.router ?? createBrowserRouter(routes));
  return (
    <ApiProvider api={deps.api} queryClient={queryClient}>
      <LiveProvider
        url={EVENTS_URL}
        eventSourceFactory={deps.eventSourceFactory}
        socketFactory={deps.webSocketFactory}
        reconnectMs={deps.streamReconnectMs}
      >
        <NavRouter router={router} />
      </LiveProvider>
    </ApiProvider>
  );
}
