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
    // Every interface, so the tailnet reaches the dev server and localhost
    // still works. Vite takes one address and Node cannot listen on two, so
    // binding the tailnet alone would cost us localhost. This also serves
    // whatever wifi the machine joins — close that off at the firewall.
    host: true,
    port,
    strictPort: true,
    // Only the port here. HMR has no host of its own: the client dials the
    // address it loaded the page from, which is the one we want.
    hmr: hmrPort ? { port: hmrPort } : undefined,
    proxy: {
      '/api': { target: orchestratorUrl, ws: true },
    },
  },
  test: {
    // Several worktrees run their suites at once, so one worker per core
    // thrashes on jsdom setup rather than computing. CI has the machine to
    // itself, so it keeps the default.
    poolOptions: process.env.CI ? {} : { forks: { maxForks: 2 } },
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // A cold CI runner is far slower than a dev machine, and the transcript
    // tests wait on a socket. The default 5 s has failed there on work that
    // passes locally every time.
    testTimeout: 15_000,
  },
});
