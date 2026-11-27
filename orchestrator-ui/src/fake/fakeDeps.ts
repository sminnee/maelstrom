import { QueryClient } from '@tanstack/react-query';
import type { AppDeps } from '../App';
import { createFakeServer, type FakeServer } from './fakeServer';
import { createFakeVoiceDevices, type FakeVoiceDevices } from './fakeVoice';
import type { Seed } from './seedWorld';

/**
 * A fake server that holds `seed`, and what `App` reaches it through. The
 * suite and the fake mode both mount `App` on this, so the two cannot differ.
 * `stepMs` is how long each operation step takes; the suite makes it short.
 */
export function fakeDeps(
  seed: Seed,
  opts: { stepMs?: number } = {},
): {
  server: FakeServer;
  queryClient: QueryClient;
  deps: AppDeps;
  voice: FakeVoiceDevices;
} {
  const server = createFakeServer({ ...seed, ...opts });
  // No retries: a refused request must fail now, not after backoff.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: 0 } },
  });
  const voice = createFakeVoiceDevices();
  return {
    server,
    queryClient,
    voice,
    deps: {
      voiceDevices: voice,
      api: server.api,
      eventSourceFactory: server.eventSourceFactory,
      webSocketFactory: server.webSocketFactory,
      // A real backoff would cost every reconnect a second of wall clock.
      streamReconnectMs: 10,
      queryClient,
    },
  };
}
