import { useMemo } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import { ApiProvider } from './api/ApiProvider';
import type { ApiClient } from './api/http';
import { createQueryClient } from './api/queryClient';
import type { EventSourceLike } from './live/changeStream';
import { LiveProvider } from './live/LiveProvider';
import type { Backend } from './protocol/backend';
import { AppShell } from './shell/AppShell';
import { BackendProvider } from './store/BackendProvider';

/** What the app reaches the server through. A test injects fakes for each. */
export interface AppDeps {
  api: ApiClient;
  eventSourceFactory?: (url: string) => EventSourceLike;
  queryClient?: QueryClient;
}

/** Where the change stream is. Same-origin: the dev proxy carries it to the server. */
export const EVENTS_URL = '/api/events';

export function App({
  backend,
  autoConnect = false,
  deps,
}: {
  backend: Backend;
  autoConnect?: boolean;
  deps: AppDeps;
}) {
  const queryClient = useMemo(() => deps.queryClient ?? createQueryClient(), [deps.queryClient]);
  return (
    <ApiProvider api={deps.api} queryClient={queryClient}>
      <LiveProvider url={EVENTS_URL} eventSourceFactory={deps.eventSourceFactory}>
        <BackendProvider backend={backend} autoConnect={autoConnect}>
          <AppShell />
        </BackendProvider>
      </LiveProvider>
    </ApiProvider>
  );
}
