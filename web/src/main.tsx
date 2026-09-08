import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// Heading face, self-hosted rather than fetched — see DESIGN.md § Typography.
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
