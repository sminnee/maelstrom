import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/tokens.css';
import './styles/base.css';
import { createApiClient } from './api/http';
import { createQueryClient } from './api/queryClient';
import { App, type AppDeps } from './App';
import type { Backend } from './protocol/backend';
import { bridgeToFakeServer } from './fake-backend/bridgeToFakeServer';
import { createFakeBackend } from './fake-backend/createFakeBackend';
import { createFakeServer } from './test/fakeServer';
import { createWsBackend } from './ws-backend/wsBackend';

// Under maelstrom the orchestrator server's URL arrives from the service's
// environment; without one the in-browser fake stands in, and shows its chip.
// The REST API and the change stream are same-origin: the dev server proxies
// `/api` to the orchestrator. The fake answers them from its own world.
const url = import.meta.env.VITE_ORCHESTRATOR_URL;
const backend: Backend = url
  ? createWsBackend({ url })
  : createFakeBackend({ seed: 7, autoplay: true });
const deps: AppDeps = url ? { api: createApiClient() } : fakeDeps(backend);

/** The fake world behind the API too, fed from the fake backend's frames. */
function fakeDeps(fake: Backend): AppDeps {
  const server = createFakeServer({ command: (cmd) => fake.command(cmd) });
  const queryClient = createQueryClient();
  bridgeToFakeServer(fake, server, queryClient);
  return { api: server.api, eventSourceFactory: server.eventSourceFactory, queryClient };
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App backend={backend} deps={deps} autoConnect />
  </StrictMode>,
);
