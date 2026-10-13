import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    restoreMocks: true,
    unstubGlobals: true,
    testTimeout: 30_000,
    // The plugin runs in Node against a real dev server; the overlay runs in jsdom.
    projects: [
      {
        extends: true,
        test: { name: 'plugin', include: ['src/plugin.test.ts'], environment: 'node' },
      },
      {
        extends: true,
        test: { name: 'overlay', include: ['src/overlay.test.ts'], environment: 'jsdom' },
      },
    ],
  },
});
