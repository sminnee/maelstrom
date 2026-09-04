import { useState } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import { ApiProvider } from './api/ApiProvider';
import type { ApiClient } from './api/http';
import { createQueryClient } from './api/queryClient';
import type { EventSourceLike } from './live/changeStream';
import { LiveProvider } from './live/LiveProvider';
import type { SocketLike } from './live/socketLike';
import { AppShell } from './shell/AppShell';

/** What the app reaches the server through. A test injects fakes for each. */
export interface AppDeps {
  api: ApiClient;
  eventSourceFactory?: (url: string) => EventSourceLike;
  /** Opens a transcript socket on a same-origin path. */
  webSocketFactory?: (path: string) => SocketLike;
  /** The first wait before a transcript socket reconnects. Tests shorten it. */
  streamReconnectMs?: number;
  queryClient?: QueryClient;
}

/** Where the change stream is. Same-origin: the dev proxy carries it to the server. */
export const EVENTS_URL = '/api/events';

export function App({ deps }: { deps: AppDeps }) {
  // One client for the life of the app: useMemo may recompute, useState never does.
  const [queryClient] = useState(() => deps.queryClient ?? createQueryClient());
  return (
    <ApiProvider api={deps.api} queryClient={queryClient}>
      <LiveProvider
        url={EVENTS_URL}
        eventSourceFactory={deps.eventSourceFactory}
        socketFactory={deps.webSocketFactory}
        reconnectMs={deps.streamReconnectMs}
      >
        <AppShell />
      </LiveProvider>
    </ApiProvider>
  );
}
