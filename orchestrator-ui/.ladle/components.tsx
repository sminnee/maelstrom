import type { GlobalProvider } from '@ladle/react';
import '../src/styles/tokens.css';
import '../src/styles/base.css';

/**
 * Stories run against the app's real tokens and base styles, so what a story
 * shows is what the app draws. Without this they would render on Ladle's own
 * white page and prove nothing.
 */
export const Provider: GlobalProvider = ({ children }) => (
  <div style={{ background: 'var(--bg)', color: 'var(--fg)', minHeight: '100vh' }}>{children}</div>
);
