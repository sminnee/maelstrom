import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/tokens.css';
import './styles/base.css';
import { createApiClient } from './api/http';
import { App, type AppDeps } from './App';
import type { Backend } from './protocol/backend';
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
const deps: AppDeps = url
  ? { api: createApiClient() }
  : (({ api, eventSourceFactory }) => ({ api, eventSourceFactory }))(createFakeServer());

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App backend={backend} deps={deps} autoConnect />
  </StrictMode>,
);
