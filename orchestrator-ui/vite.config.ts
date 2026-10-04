import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import type { Plugin } from 'vite';
import { maelJig } from './vite-plugin-mael-jig/plugin';

// Under maelstrom the dev server binds the worktree's FRONTEND port, and HMR
// its FRONTEND_HMR port, so several worktrees can serve at once.
const port = Number(process.env.FRONTEND_PORT) || 5173;
const hmrPort = Number(process.env.FRONTEND_HMR_PORT) || undefined;
// The orchestrator server behind `/api`: REST and the per-agent WebSockets.
// Read here, not by the bundle, so the built app carries no address.
const orchestratorUrl = process.env.ORCHESTRATOR_URL ?? 'http://localhost:8765';
// Vite answers 403 to a Host header it does not know, so the dev host must be allowed.
const devHost = process.env.DEV_HOST;
const allowedHosts = devHost ? [devHost] : undefined;
// The change stream dials the orchestrator directly, so the dev server hands
// the bundle its port. Set on `process.env` under vite's own `VITE_` prefix
// rather than through `define`, which would rewrite `import.meta.env` for the
// test runner too. See docs/dev/orchestrator-ui.md.
//
// Dev only. A built page carries no address — the orchestrator would serve it
// same-origin — so `eventsUrl` falls back to a relative URL there.

// The fake mode: `web-fake` sets FAKE_MODE, and `/` serves `preview.html`.
// See docs/dev/orchestrator-ui.md.
const fakeMode = process.env.FAKE_MODE === '1';
const serveFake: Plugin = {
  name: 'mael-serve-fake',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url === '/' || req.url?.startsWith('/?')) {
        req.url = `/preview.html${req.url.slice(1)}`;
      }
      next();
    });
  },
};

export default defineConfig(({ command }) => {
  if (command === 'serve') {
    process.env.VITE_ORCHESTRATOR_PORT = new URL(orchestratorUrl).port;
  }
  return {
    plugins: [
      react(),
      ...(fakeMode ? [serveFake] : []),
      // The jig (CONTEXT.md), on the dev server and Ladle but not under
      // vitest, which serves no page. Send needs the orchestrator named
      // explicitly: the default above is a guess, fine for a proxy, wrong for a post.
      ...(!process.env.VITEST ? [maelJig({ orchestratorUrl: process.env.ORCHESTRATOR_URL })] : []),
    ],
    server: {
      // Every interface, so the tailnet reaches the dev server and localhost
      // still works. Vite takes one address and Node cannot listen on two, so
      // binding the tailnet alone would cost us localhost. This also serves
      // whatever wifi the machine joins — close that off at the firewall.
      host: true,
      allowedHosts,
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
      // cores, so the default fans the files across them and the slowest one
      // starves: the former single `App.test.tsx` took 19 s on a green run and
      // timed out at 24 s and 28 s on the next two, a different test each
      // time. It is now nine `App.*.test.tsx` files, none of them the outlier
      // that starved, but the cap still earns its place while a runner has two
      // cores.
      poolOptions: { forks: { maxForks: 2 } },
      globals: false,
      // A layout spy left standing leaks into whatever file runs next, and
      // surfaces as an unrelated test failing in file order. Two suites had
      // grown the same `afterEach(vi.restoreAllMocks())` to work around this.
      restoreMocks: true,
      // The same for a stubbed global: a test that fails before its own
      // unstub would leave a fake `location` for every test after it.
      unstubGlobals: true,
      // A cold CI runner is far slower than a dev machine. This ceiling was
      // raised four times chasing flakes that splitting `App.test.tsx` and
      // setting `asyncUtilTimeout` (see src/test/setup.ts) turned out to
      // explain, so it is now provisional: no test waits on it deliberately,
      // and it only bounds a hang. Lower it once CI has run green on the split
      // for a while -- but measure first, because raising it has never once
      // fixed anything here.
      testTimeout: 30_000,
      // The jig's plugin runs in Node against a real dev server, so it stays
      // out of the app's jsdom and its setup file.
      projects: [
        {
          extends: true,
          test: {
            name: 'app',
            include: ['src/**/*.test.{ts,tsx}'],
            environment: 'jsdom',
            setupFiles: ['./src/test/setup.ts'],
            css: false,
          },
        },
        {
          extends: true,
          test: {
            name: 'jig',
            include: ['vite-plugin-mael-jig/**/*.test.ts'],
            environment: 'node',
          },
        },
      ],
    },
  };
});
