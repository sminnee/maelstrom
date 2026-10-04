import { execFileSync } from 'node:child_process';
import { once } from 'node:events';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createServer, type ViteDevServer } from 'vite';
import { WebSocketServer, type WebSocket as ProviderSocket } from 'ws';
import { maelJig } from './plugin';

// As `mkdtemp` names it. On macOS that is under `/var`, whose real path is
// `/private/var`, so a lookup by path must compare real paths.
let worktree: string;
let server: ViteDevServer;
let origin: string;
const sockets: WebSocket[] = [];
const closers: (() => void)[] = [];

async function start(providerUrl?: string) {
  server = await createServer({
    root: worktree,
    configFile: false,
    logLevel: 'silent',
    plugins: [maelJig({ providerUrl })],
    server: { port: 0, host: '127.0.0.1' },
  });
  await server.listen();
  origin = `http://127.0.0.1:${(server.httpServer?.address() as AddressInfo).port}`;
}

beforeEach(() => {
  worktree = mkdtempSync(join(tmpdir(), 'mael-jig-'));
  execFileSync('git', ['init', '-q'], { cwd: worktree });
  writeFileSync(join(worktree, 'index.html'), '<html><head></head><body></body></html>');
});

afterEach(async () => {
  sockets.splice(0).forEach((socket) => socket.close());
  closers.splice(0).forEach((close) => close());
  await server.close();
  rmSync(worktree, { recursive: true, force: true });
});

type Frame = { type: string; [key: string]: unknown };

/**
 * A jig provider: a socket server that records each frame the plugin sends,
 * and answers each feedback frame with `reply`, or leaves it unanswered for `null`.
 */
async function fakeProvider(
  reply: { ok: boolean; body: unknown } | null = {
    ok: true,
    body: { agentIds: ['ag1'], refused: [] },
  },
) {
  const wss = new WebSocketServer({ port: 0, host: '127.0.0.1' });
  await once(wss, 'listening');
  closers.push(() => wss.close());
  const frames: Frame[] = [];
  let socket: ProviderSocket | undefined;
  let wake = () => {};
  wss.on('connection', (ws) => {
    socket = ws;
    ws.on('message', (data) => {
      const frame = JSON.parse(String(data)) as Frame;
      frames.push(frame);
      wake();
      if (frame.type === 'feedback' && reply) {
        ws.send(JSON.stringify({ type: 'reply', id: frame.id, ...reply }));
      }
    });
  });
  /** The `nth` frame of `type`, once it arrives. */
  const frame = async (type: string, nth = 1): Promise<Frame> => {
    for (;;) {
      const found = frames.filter((f) => f.type === type)[nth - 1];
      if (found) return found;
      await new Promise<void>((resolve) => (wake = resolve));
    }
  };
  return {
    url: `ws://127.0.0.1:${(wss.address() as AddressInfo).port}/api/jig`,
    frames,
    frame,
    send: (sent: Frame) => socket?.send(JSON.stringify(sent)),
    drop: () => socket?.terminate(),
  };
}

const patchFile = () => join(worktree, '.drafts', 'monkeypatch.css');

type Update = { css: string; from: string | null };

/** A page's HMR socket, once Vite has greeted it. `next()` is the next event of `name`. */
async function hmrSocket<T>(name: string) {
  const socket = new WebSocket(origin.replace('http', 'ws'), 'vite-hmr');
  sockets.push(socket);
  const events: T[] = [];
  let wake = () => {};
  let connect = () => {};
  const connected = new Promise<void>((resolve) => (connect = resolve));
  socket.onmessage = (message) => {
    const payload = JSON.parse(String(message.data));
    if (payload.type === 'connected') connect();
    if (payload.type === 'custom' && payload.event === name) {
      events.push(payload.data);
      wake();
    }
  };
  const next = async (): Promise<T> => {
    while (!events.length) await new Promise<void>((resolve) => (wake = resolve));
    return events.shift()!;
  };
  await connected;
  return { events, next };
}

/**
 * A page's HMR socket, once the watcher has seen the monkeypatch file.
 * `next()` is the next `mael-jig:monkeypatch` after that. The file must not
 * exist yet, and does not exist after.
 *
 * The watcher picks up `.drafts/` a moment after the server starts, and a
 * change before then raises no event. So the page writes a marker until one
 * comes back, rather than sleeping for a guess at the moment.
 */
async function hmrPage() {
  const { events: updates, next } = await hmrSocket<Update>('mael-jig:monkeypatch');
  const marker = '/* watched */';
  while (!updates.some((u) => u.css === marker)) {
    writeFileSync(patchFile(), marker);
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  rmSync(patchFile());
  for (let update = await next(); update.css !== ''; update = await next());
  return { next };
}

const put = (css: string, client = 'c1') =>
  fetch(`${origin}/__mael/monkeypatch`, {
    method: 'PUT',
    headers: { 'x-mael-jig-client': client },
    body: css,
  });

const post = (path: string, body = '') => fetch(`${origin}${path}`, { method: 'POST', body });

const sendFeedback = (body: unknown) => post('/__mael/feedback', JSON.stringify(body));

const monkeypatchGet = async () => (await fetch(`${origin}/__mael/monkeypatch`)).json();

describe('the jig plugin', () => {
  beforeEach(() => start());

  it('writes a PUT to the monkeypatch file and reads it back', async () => {
    expect((await put('.chip { padding: 14px; }')).status).toBe(204);
    expect(readFileSync(patchFile(), 'utf8')).toBe('.chip { padding: 14px; }');

    expect(await monkeypatchGet()).toEqual({ css: '.chip { padding: 14px; }', visible: false });
  });

  it('reads an absent file as no CSS', async () => {
    expect(await monkeypatchGet()).toEqual({ css: '', visible: false });
  });

  it('writes the file after .drafts is cleaned away under the running server', async () => {
    rmSync(join(worktree, '.drafts'), { recursive: true });

    expect((await put('.chip{padding:2px}')).status).toBe(204);
    expect(readFileSync(patchFile(), 'utf8')).toBe('.chip{padding:2px}');
  });

  it('sends every page the file when someone else changes it', async () => {
    const page = await hmrPage();

    writeFileSync(patchFile(), '.row { gap: 4px; }');

    expect(await page.next()).toEqual({ css: '.row { gap: 4px; }', from: null });
  });

  it('names the page that wrote the file, so that page can ignore the echo', async () => {
    const page = await hmrPage();

    await put('.chip{padding:20px}');

    expect(await page.next()).toEqual({ css: '.chip{padding:20px}', from: 'c1' });
  });

  it('sends no CSS when the agent deletes the file', async () => {
    const page = await hmrPage();
    writeFileSync(patchFile(), '.chip{padding:20px}');
    expect((await page.next()).css).toBe('.chip{padding:20px}');

    rmSync(patchFile());

    expect(await page.next()).toEqual({ css: '', from: null });
  });

  it('injects the overlay into the page', async () => {
    const html = await (await fetch(`${origin}/`)).text();
    expect(html).toMatch(/<script type="module" src="[^"]*client\.ts"><\/script>/);
  });

  it('refuses a send and a hide when no provider is set', async () => {
    expect((await sendFeedback({ css: '.chip{padding:20px}' })).status).toBe(503);
    expect((await post('/__mael/hide')).status).toBe(503);
  });

  it('refuses a method each route does not take', async () => {
    expect((await fetch(`${origin}/__mael/monkeypatch`, { method: 'DELETE' })).status).toBe(405);
    expect((await fetch(`${origin}/__mael/feedback`)).status).toBe(405);
    expect((await fetch(`${origin}/__mael/hide`)).status).toBe(405);
  });
});

describe('the provider', () => {
  it('is greeted with the worktree path', async () => {
    const provider = await fakeProvider();
    await start(provider.url);

    expect(await provider.frame('hello')).toEqual({ type: 'hello', path: realpathSync(worktree) });
  });

  it('shows the jig on every page when it says so', async () => {
    const provider = await fakeProvider();
    await start(provider.url);
    await provider.frame('hello');
    const page = await hmrSocket<{ visible: boolean }>('mael-jig:state');

    provider.send({ type: 'state', visible: true });

    expect(await page.next()).toEqual({ visible: true });
    expect(await monkeypatchGet()).toEqual({ css: '', visible: true });
  });

  it('hides the jig when its socket goes down', async () => {
    const provider = await fakeProvider();
    await start(provider.url);
    await provider.frame('hello');
    const page = await hmrSocket<{ visible: boolean }>('mael-jig:state');
    provider.send({ type: 'state', visible: true });
    await page.next();

    provider.drop();

    expect(await page.next()).toEqual({ visible: false });
    expect(await monkeypatchGet()).toEqual({ css: '', visible: false });
  });

  it('reconnects, greets the provider again and follows its state', async () => {
    const provider = await fakeProvider();
    await start(provider.url);
    await provider.frame('hello');
    const page = await hmrSocket<{ visible: boolean }>('mael-jig:state');

    provider.drop();
    await provider.frame('hello', 2);
    provider.send({ type: 'state', visible: true });

    expect(await page.next()).toEqual({ visible: true });
  });

  it('answers 503 when the provider goes away before it replies', async () => {
    const provider = await fakeProvider(null);
    await start(provider.url);
    await provider.frame('hello');

    const sent = sendFeedback({ css: '.chip{padding:20px}' });
    await provider.frame('feedback');
    provider.drop();

    expect((await sent).status).toBe(503);
  });

  it('relays feedback and answers with the reply', async () => {
    const provider = await fakeProvider();
    await start(provider.url);
    await provider.frame('hello');
    const feedback = { type: 'monkeypatch', css: '.chip{padding:20px}', note: 'tighter' };

    const sent = await sendFeedback(feedback);

    expect(sent.status).toBe(200);
    expect(await sent.json()).toEqual({ agentIds: ['ag1'], refused: [] });
    expect(await provider.frame('feedback')).toEqual({
      type: 'feedback',
      id: expect.any(Number),
      feedback,
    });
  });

  it('passes a refusal through', async () => {
    const refusal = { error: { code: 'invalid', message: 'No agent is running in x' } };
    const provider = await fakeProvider({ ok: false, body: refusal });
    await start(provider.url);
    await provider.frame('hello');

    const sent = await sendFeedback({ css: '.chip{padding:20px}' });

    expect(sent.status).toBe(400);
    expect(await sent.json()).toEqual(refusal);
  });

  it('sends a hide', async () => {
    const provider = await fakeProvider();
    await start(provider.url);
    await provider.frame('hello');

    expect((await post('/__mael/hide')).status).toBe(204);
    expect(await provider.frame('hide')).toEqual({ type: 'hide' });
  });
});
