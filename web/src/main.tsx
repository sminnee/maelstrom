import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// The heading face, Latin subset at the one weight headings use (~22kB). Self
// hosted through the bundle rather than fetched from a CDN: this app is reached
// over a tailnet and binds local ports, so an operator console must not need
// the public internet to draw its own headings. `--font` remains the fallback,
// so a failed fetch renders exactly what shipped before this face existed.
import '@fontsource/inter-tight/latin-600.css';
import './styles/tokens.css';
import './styles/base.css';
import { createApiClient } from './api/http';
import { App } from './App';

// The REST API, the change stream and the transcript sockets are all
// same-origin: the dev server proxies `/api` to the orchestrator.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App deps={{ api: createApiClient() }} />
  </StrictMode>,
);
