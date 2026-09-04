import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
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
