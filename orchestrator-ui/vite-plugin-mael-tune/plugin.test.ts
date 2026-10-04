import { execFileSync } from 'node:child_process';
import { createServer as createHttpServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createServer, type ViteDevServer } from 'vite';
import { maelTune } from './plugin';

// As `mkdtemp` names it. On macOS that is under `/var`, whose real path is
// `/private/var`, so a lookup by path must compare real paths.
let worktree: string;
let server: ViteDevServer;
let origin: string;
const sockets: WebSocket[] = [];
const closers: (() => void)[] = [];

async function start(orchestratorUrl?: string) {
  server = await createServer({
    root: worktree,
    configFile: false,
    logLevel: 'silent',
    plugins: [maelTune({ orchestratorUrl })],
    server: { port: 0, host: '127.0.0.1' },
  });
  await server.listen();
  origin = `http://127.0.0.1:${(server.httpServer?.address() as AddressInfo).port}`;
}

beforeEach(() => {
  worktree = mkdtempSync(join(tmpdir(), 'mael-tune-'));
  execFileSync('git', ['init', '-q'], { cwd: worktree });
  writeFileSync(join(worktree, 'index.html'), '<html><head></head><body></body></html>');
});

afterEach(async () => {
  sockets.splice(0).forEach((socket) => socket.close());
  closers.splice(0).forEach((close) => close());
  await server.close();
  rmSync(worktree, { recursive: true, force: true });
});

/**
 * An orchestrator that lists one worktree at `path`, records each post and
 * answers it with `reply`.
 */
async function fakeOrchestrator(
  path: string,
  reply: { status: number; body: unknown } = {
    status: 200,
    body: { agentIds: ['ag1'], refused: [] },
  },
) {
  const posts: { url: string; body: unknown }[] = [];
  const orchestrator = createHttpServer((req, res) => {
    let text = '';
    req.on('data', (chunk) => (text += chunk));
    req.on('end', () => {
      res.setHeader('content-type', 'application/json');
      if (req.method === 'GET') {
        const worktrees = [
          { id: 'northwind-bravo', path: '/elsewhere' },
          { id: 'northwind-alpha', path },
        ];
        res.end(JSON.stringify({ worktrees }));
        return;
      }
      posts.push({ url: req.url ?? '', body: JSON.parse(text) });
      res.statusCode = reply.status;
      res.end(JSON.stringify(reply.body));
    });
  });
  await new Promise<void>((resolve) => orchestrator.listen(0, '127.0.0.1', resolve));
  closers.push(() => orchestrator.close());
  return { url: `http://127.0.0.1:${(orchestrator.address() as AddressInfo).port}`, posts };
}

const tuningFile = () => join(worktree, '.drafts', 'tuning.css');

type Update = { css: string; from: string | null };

/**
 * A page's HMR socket, once Vite has greeted it and the watcher has seen the
 * tuning file. `next()` is the next `mael-tune:update` after that.
 *
 * The watcher picks up `.drafts/` a moment after the server starts, and a
 * change before then raises no event. So the page writes a marker until one
 * comes back, rather than sleeping for a guess at the moment.
 */
async function hmrPage() {
  const socket = new WebSocket(origin.replace('http', 'ws'), 'vite-hmr');
  sockets.push(socket);
  const updates: Update[] = [];
  let wake = () => {};
  let connect = () => {};
  const connected = new Promise<void>((resolve) => (connect = resolve));
  socket.onmessage = (message) => {
    const payload = JSON.parse(String(message.data));
    if (payload.type === 'connected') connect();
    if (payload.type === 'custom' && payload.event === 'mael-tune:update') {
      updates.push(payload.data);
      wake();
    }
  };
  const next = async (): Promise<Update> => {
    while (!updates.length) await new Promise<void>((resolve) => (wake = resolve));
    return updates.shift()!;
  };

  await connected;
  const marker = '/* watched */';
  const before = existsSync(tuningFile()) ? readFileSync(tuningFile(), 'utf8') : null;
  while (!updates.some((u) => u.css === marker)) {
    writeFileSync(tuningFile(), marker);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (before === null) rmSync(tuningFile());
  else writeFileSync(tuningFile(), before);
  // Drain the markers and the restore, so `next()` starts after them.
  const restored = before ?? '';
  for (let update = await next(); update.css !== restored; update = await next());
  return { next };
}

const put = (css: string, client = 'c1') =>
  fetch(`${origin}/__mael/tuning`, {
    method: 'PUT',
    headers: { 'x-mael-tune-client': client },
    body: css,
  });

const sendCss = (body: unknown) =>
  fetch(`${origin}/__mael/send`, { method: 'POST', body: JSON.stringify(body) });

describe('the tuning jig plugin', () => {
  beforeEach(() => start());

  it('writes a PUT to the tuning file and reads it back', async () => {
    expect((await put('.chip { padding: 14px; }')).status).toBe(204);
    expect(readFileSync(tuningFile(), 'utf8')).toBe('.chip { padding: 14px; }');

    const got = await fetch(`${origin}/__mael/tuning`);
    expect(await got.json()).toEqual({ css: '.chip { padding: 14px; }', canSend: false });
  });

  it('reads an absent file as no CSS', async () => {
    const got = await fetch(`${origin}/__mael/tuning`);
    expect(await got.json()).toEqual({ css: '', canSend: false });
  });

  it('writes the file after .drafts is cleaned away under the running server', async () => {
    rmSync(join(worktree, '.drafts'), { recursive: true });

    expect((await put('.chip{padding:2px}')).status).toBe(204);
    expect(readFileSync(tuningFile(), 'utf8')).toBe('.chip{padding:2px}');
  });

  it('sends every page the file when someone else changes it', async () => {
    const page = await hmrPage();

    writeFileSync(tuningFile(), '.row { gap: 4px; }');

    expect(await page.next()).toEqual({ css: '.row { gap: 4px; }', from: null });
  });

  it('names the page that wrote the file, so that page can ignore the echo', async () => {
    const page = await hmrPage();

    await put('.chip{padding:20px}');

    expect(await page.next()).toEqual({ css: '.chip{padding:20px}', from: 'c1' });
  });

  it('sends no CSS when the agent deletes the file', async () => {
    writeFileSync(tuningFile(), '.chip{padding:20px}');
    const page = await hmrPage();

    rmSync(tuningFile());

    expect(await page.next()).toEqual({ css: '', from: null });
  });

  it('injects the overlay into the page', async () => {
    const html = await (await fetch(`${origin}/`)).text();
    expect(html).toMatch(/<script type="module" src="[^"]*client\.ts"><\/script>/);
  });

  it('refuses a send when no orchestrator is set', async () => {
    expect((await sendCss({ css: '.chip{padding:20px}' })).status).toBe(503);
  });

  it('refuses a method each route does not take', async () => {
    expect((await fetch(`${origin}/__mael/tuning`, { method: 'DELETE' })).status).toBe(405);
    expect((await fetch(`${origin}/__mael/send`)).status).toBe(405);
  });
});

describe('send', () => {
  it('turns Send on when an orchestrator is set', async () => {
    await start('http://127.0.0.1:1');

    const got = await fetch(`${origin}/__mael/tuning`);
    expect(await got.json()).toEqual({ css: '', canSend: true });
  });

  it('posts to the worktree the orchestrator lists at this path', async () => {
    const orchestrator = await fakeOrchestrator(realpathSync(worktree));
    await start(orchestrator.url);

    const sent = await sendCss({ css: '.chip{padding:20px}', note: 'tighter' });

    expect(sent.status).toBe(200);
    expect(await sent.json()).toEqual({ agentIds: ['ag1'], refused: [] });
    expect(orchestrator.posts).toEqual([
      {
        url: '/api/worktrees/northwind-alpha/tuning',
        body: { css: '.chip{padding:20px}', note: 'tighter' },
      },
    ]);
  });

  it('passes an orchestrator refusal through', async () => {
    const refusal = { error: { code: 'invalid', message: 'No agent is running in x' } };
    const orchestrator = await fakeOrchestrator(realpathSync(worktree), {
      status: 400,
      body: refusal,
    });
    await start(orchestrator.url);

    const sent = await sendCss({ css: '.chip{padding:20px}' });

    expect(sent.status).toBe(400);
    expect(await sent.json()).toEqual(refusal);
  });

  it('refuses a send when the orchestrator lists no worktree at this path', async () => {
    const orchestrator = await fakeOrchestrator('/somewhere/else');
    await start(orchestrator.url);

    const sent = await sendCss({ css: '.chip{padding:20px}' });

    expect(sent.status).toBe(404);
    expect(orchestrator.posts).toEqual([]);
  });
});
