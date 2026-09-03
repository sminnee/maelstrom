import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/tokens.css';
import './styles/base.css';
import { App } from './App';
import type { Backend } from './protocol/backend';
import { createFakeBackend } from './fake-backend/createFakeBackend';
import { createWsBackend } from './ws-backend/wsBackend';

// Under maelstrom the orchestrator server's URL arrives from the service's
// environment; without one the in-browser fake stands in, and shows its chip.
const url = import.meta.env.VITE_ORCHESTRATOR_URL;
const backend: Backend = url
  ? createWsBackend({ url })
  : createFakeBackend({ seed: 7, autoplay: true });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App backend={backend} autoConnect />
  </StrictMode>,
);
