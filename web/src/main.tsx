import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/tokens.css';
import './styles/base.css';
import { App } from './App';
import { createFakeBackend } from './fake-backend/createFakeBackend';

const backend = createFakeBackend({ seed: 7, autoplay: true });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App backend={backend} autoConnect />
  </StrictMode>,
);
