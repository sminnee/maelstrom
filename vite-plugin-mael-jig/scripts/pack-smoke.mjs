// The pack smoke: build and pack the package, install the tarball into a new
// Vite app, start its dev server, and check that each page loads the jig.
// It proves what a consumer gets from npm: the built JS, not the source.
//
// Usage: pnpm smoke. SMOKE_VITE=6 runs it against another Vite.
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const pkg = dirname(dirname(fileURLToPath(import.meta.url)));
const manifest = JSON.parse(readFileSync(join(pkg, 'package.json'), 'utf8'));
const work = mkdtempSync(join(tmpdir(), 'mael-jig-smoke-'));
const run = (cmd, args, cwd) => execFileSync(cmd, args, { cwd, stdio: 'inherit' });

// The page check. It runs inside the app, so it imports the app's own copies.
const check = `
import { createServer } from 'vite';
import { maelJig } from 'vite-plugin-mael-jig';

const server = await createServer({
  configFile: false,
  logLevel: 'error',
  plugins: [maelJig()],
  server: { port: 0, host: '127.0.0.1' },
});
await server.listen();
const origin = 'http://127.0.0.1:' + server.httpServer.address().port;
const status = async (path) => (await fetch(origin + path)).status;
try {
  const html = await (await fetch(origin + '/')).text();
  const src = /<script type="module" src="([^"]*mael-jig[^"]*)">/.exec(html)?.[1];
  if (!src) throw new Error('the page has no jig script');
  const client = await fetch(origin + src);
  if (client.status !== 200) throw new Error('the client answers ' + client.status);
  const overlay = /from "([^"]+overlay\\.js[^"]*)"/.exec(await client.text())?.[1];
  if (!overlay) throw new Error('the client imports no overlay.js');
  if ((await status(overlay)) !== 200) throw new Error('the overlay does not serve');
  if ((await status('/__mael/state')) !== 200) throw new Error('/__mael/state does not answer');
  console.log('pack smoke: the page loads the jig');
} catch (error) {
  console.error('pack smoke: ' + error.message);
  process.exitCode = 1;
} finally {
  await server.close();
}
`;

try {
  run('pnpm', ['run', 'build'], pkg);
  run('pnpm', ['pack', '--pack-destination', work], pkg);
  const tarball = join(work, `${manifest.name}-${manifest.version}.tgz`);

  const app = join(work, 'app');
  mkdirSync(app);
  writeFileSync(
    join(app, 'package.json'),
    JSON.stringify({
      name: 'smoke',
      private: true,
      type: 'module',
      dependencies: {
        vite: process.env.SMOKE_VITE ?? manifest.devDependencies.vite,
        [manifest.name]: `file:${tarball}`,
      },
    }),
  );
  writeFileSync(join(app, 'index.html'), '<html><head></head><body></body></html>');
  writeFileSync(join(app, 'check.mjs'), check);
  // The jig serves only inside a git checkout.
  run('git', ['init', '-q'], app);
  run('pnpm', ['install', '--prefer-offline', '--ignore-workspace'], app);

  const env = { ...process.env };
  delete env.VITEST;
  delete env.MAEL_FEEDBACK_URL;
  execFileSync('node', ['check.mjs'], { cwd: app, stdio: 'inherit', env });
} finally {
  rmSync(work, { recursive: true, force: true });
}
