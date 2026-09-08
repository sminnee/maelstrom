import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Under maelstrom the dev server binds the worktree's FRONTEND port, and HMR
// its FRONTEND_HMR port, so several worktrees can serve at once.
const port = Number(process.env.FRONTEND_PORT) || 5173;
const hmrPort = Number(process.env.FRONTEND_HMR_PORT) || undefined;
// The orchestrator server behind `/api`: REST and the per-agent WebSockets.
// Read here, not by the bundle, so the built app carries no address.
const orchestratorUrl = process.env.ORCHESTRATOR_URL ?? 'http://localhost:8765';
// The change stream dials the orchestrator directly, so the dev server hands
// the bundle its port. Set on `process.env` under vite's own `VITE_` prefix
// rather than through `define`, which would rewrite `import.meta.env` for the
// test runner too. See docs/dev/orchestrator-ui.md.
//
// Dev only. A built page carries no address — the orchestrator would serve it
// same-origin — so `eventsUrl` falls back to a relative URL there.
export default defineConfig(({ command }) => {
  if (command === 'serve') {
    process.env.VITE_ORCHESTRATOR_PORT = new URL(orchestratorUrl).port;
  }
  return {
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
      // thrashes on jsdom setup rather than computing. A GitHub runner is two
      // cores, so the default fans ~35 files across them and the slowest file
      // starves: `App.test.tsx` took 19s on a green run and timed out at 24s
      // and 28s on the next two, a different test each time. Cap both.
      poolOptions: { forks: { maxForks: 2 } },
      environment: 'jsdom',
      globals: false,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
      // A cold CI runner is far slower than a dev machine, and the transcript
      // tests wait on a socket. The default 5 s has failed there on work that
      // passes locally every time. 15 s was still not enough: `App.test.tsx`
      // runs in 7.5 s alone on a dev machine and 25 s on a two-core runner, so
      // a wait inside it can sit for 10 s while the other fork holds the core.
      testTimeout: 30_000,
    },
  };
});
