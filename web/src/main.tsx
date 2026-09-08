import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/tokens.css';
import './styles/base.css';
import { createApiClient } from './api/http';
import { App } from './App';

// The REST API and the transcript sockets are same-origin: the dev server
// proxies `/api` to the orchestrator. The change stream is not — see
// `eventsUrl`.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App deps={{ api: createApiClient() }} />
  </StrictMode>,
);
