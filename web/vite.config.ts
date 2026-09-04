import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Under maelstrom the dev server binds the worktree's FRONTEND port, and HMR
// its FRONTEND_HMR port, so several worktrees can serve at once.
const port = Number(process.env.FRONTEND_PORT) || 5173;
const hmrPort = Number(process.env.FRONTEND_HMR_PORT) || undefined;
// The orchestrator server behind `/api`: REST, the change stream, and the
// per-agent WebSockets. Read here, not by the bundle, so the built app
// carries no address.
const orchestratorUrl = process.env.ORCHESTRATOR_URL ?? 'http://localhost:8765';

export default defineConfig({
  plugins: [react()],
  server: {
    port,
    strictPort: true,
    hmr: hmrPort ? { port: hmrPort } : undefined,
    proxy: {
      '/api': { target: orchestratorUrl, ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
