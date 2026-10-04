// The jig's dev-server half. See CONTEXT.md, "Jig", and
// docs/dev/orchestrator-ui.md, "The jig".
//
// The jig owns its protocol with a **Jig provider**: JSON frames over one
// WebSocket, at `MAEL_JIG_URL`.
//
//   jig → provider
//     {type: "hello", path}             the git top level, sent on each open
//     {type: "feedback", id, feedback}  the body `POST /__mael/feedback` took
//     {type: "hide"}                    the user hid the jig
//   provider → jig
//     {type: "state", visible}          after the hello, and on each change
//     {type: "reply", id, ok, body}     one per feedback frame
//
// The jig is shown only while the socket is open and the provider says so.
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Plugin } from 'vite';

interface Options {
  /** The jig provider's WebSocket URL. Unset or empty, the jig is off. */
  providerUrl?: string;
}

interface Reply {
  type: 'reply';
  id: number;
  ok: boolean;
  body: unknown;
}

/** The custom HMR event that carries the file to every page. */
const UPDATE_EVENT = 'mael-jig:monkeypatch';
/** The custom HMR event that tells every page whether to draw the jig. */
const STATE_EVENT = 'mael-jig:state';
const BACKOFF_MS = [250, 1000, 5000];
const CLIENT_HEADER = 'x-mael-jig-client';
const SETTLE_MS = 80;
const CLIENT = join(dirname(fileURLToPath(import.meta.url)), 'client.ts');

export function maelJig({ providerUrl }: Options): Plugin {
  // Empty until the server starts, and for good when it runs outside a git
  // checkout: the jig is a convenience, so it switches off rather than stop the server.
  let file = '';
  // The last PUT, so a watcher event it caused names its page.
  let lastWrite: { css: string; from: string } | null = null;

  const read = () => {
    try {
      return readFileSync(file, 'utf8');
    } catch {
      return '';
    }
  };

  return {
    name: 'mael-jig',
    apply: 'serve',
    configureServer(server) {
      const worktree = gitToplevel(server.config.root);
      if (!worktree) {
        server.config.logger.warn('mael-jig: not in a git checkout, so the jig is off');
        return;
      }
      const drafts = join(worktree, '.drafts');
      file = join(drafts, 'monkeypatch.css');
      // The directory, not the file: a file that does not exist yet is not watched.
      mkdirSync(drafts, { recursive: true });
      server.watcher.add(drafts);
      // Read a moment after the last event, not at it. A write truncates the file
      // first, so an event can see it empty, and the watcher drops a second
      // `change` that follows within 50 ms: the pages would keep the empty read.
      let settle: ReturnType<typeof setTimeout> | undefined;
      const broadcast = (changed: string) => {
        if (changed !== file) return;
        clearTimeout(settle);
        settle = setTimeout(() => {
          const css = read();
          const from = lastWrite?.css === css ? lastWrite.from : null;
          server.ws.send({ type: 'custom', event: UPDATE_EVENT, data: { css, from } });
        }, SETTLE_MS);
      };
      server.watcher.on('add', broadcast);
      server.watcher.on('change', broadcast);
      server.watcher.on('unlink', broadcast);

      const provider = connect(usableUrl(providerUrl, server.config.logger), worktree, (visible) =>
        server.ws.send({ type: 'custom', event: STATE_EVENT, data: { visible } }),
      );
      server.httpServer?.once('close', provider.stop);

      server.middlewares.use('/__mael/monkeypatch', (req, res) => {
        if (req.method === 'GET') {
          json(res, 200, { css: read(), visible: provider.visible() });
          return;
        }
        if (req.method !== 'PUT') {
          json(res, 405, { error: { message: `${req.method} is not allowed` } });
          return;
        }
        void body(req)
          .then((css) => {
            lastWrite = { css, from: String(req.headers[CLIENT_HEADER] ?? '') };
            // Again on each write: `.drafts/` can be cleaned away under a running server.
            mkdirSync(drafts, { recursive: true });
            writeFileSync(file, css);
            res.statusCode = 204;
            res.end();
          })
          .catch((error: unknown) => json(res, 500, { error: { message: String(error) } }));
      });

      server.middlewares.use('/__mael/feedback', (req, res) => {
        if (req.method !== 'POST') {
          json(res, 405, { error: { message: `${req.method} is not allowed` } });
          return;
        }
        void body(req)
          .then((sent) => {
            let feedback: unknown;
            try {
              feedback = JSON.parse(sent);
            } catch {
              json(res, 400, { error: { message: 'The feedback is not JSON' } });
              return;
            }
            // Through a promise, so a throw for no socket answers 503 too.
            return Promise.resolve(feedback)
              .then((sent) => provider.feedback(sent))
              .then(
                (reply) => json(res, reply.ok ? 200 : 400, reply.body),
                (error: unknown) => json(res, 503, { error: { message: message(error) } }),
              );
          })
          .catch((error: unknown) => json(res, 500, { error: { message: message(error) } }));
      });

      server.middlewares.use('/__mael/hide', (req, res) => {
        if (req.method !== 'POST') {
          json(res, 405, { error: { message: `${req.method} is not allowed` } });
          return;
        }
        try {
          provider.hide();
          res.statusCode = 204;
          res.end();
        } catch (error) {
          json(res, 503, { error: { message: message(error) } });
        }
      });
    },
    // The file is not a module, so nothing reloads for it: the event above is the update.
    handleHotUpdate({ file: changed }) {
      if (changed === file) return [];
    },
    transformIndexHtml() {
      if (!file) return;
      return [{ tag: 'script', attrs: { type: 'module', src: `/@fs${CLIENT}` }, injectTo: 'body' }];
    },
  };
}

/**
 * The socket to the provider, kept open: it reconnects with backoff, and
 * reports the jig hidden while it is down. `onState` hears each change.
 */
function connect(url: string | undefined, worktree: string, onState: (visible: boolean) => void) {
  let socket: WebSocket | null = null;
  let visible = false;
  let stopped = !url;
  let attempt = 0;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let nextId = 1;
  const waiting = new Map<
    number,
    { resolve: (reply: Reply) => void; reject: (e: Error) => void }
  >();

  const show = (next: boolean) => {
    if (next === visible) return;
    visible = next;
    onState(visible);
  };

  const open = () => {
    if (stopped || !url) return;
    const ws = new WebSocket(url);
    socket = ws;
    ws.onopen = () => {
      attempt = 0;
      ws.send(JSON.stringify({ type: 'hello', path: worktree }));
    };
    ws.onmessage = (event) => {
      // A throw here would be uncaught, and would take the dev server down.
      let frame;
      try {
        frame = JSON.parse(String(event.data));
      } catch {
        return;
      }
      if (typeof frame !== 'object' || frame === null) return;
      if (frame.type === 'state') show(Boolean(frame.visible));
      if (frame.type === 'reply') {
        waiting.get(frame.id)?.resolve(frame);
        waiting.delete(frame.id);
      }
    };
    ws.onclose = () => {
      socket = null;
      show(false);
      for (const { reject } of waiting.values()) reject(new Error('The jig provider went away'));
      waiting.clear();
      if (stopped) return;
      retry = setTimeout(open, BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)]);
    };
  };

  /** The open socket, or a throw that says why there is none. */
  const live = () => {
    if (socket?.readyState !== WebSocket.OPEN) {
      throw new Error(url ? 'The jig provider is not reachable' : 'No jig provider is set');
    }
    return socket;
  };

  open();
  return {
    visible: () => visible,
    feedback(feedback: unknown): Promise<Reply> {
      const ws = live();
      const id = nextId++;
      return new Promise((resolve, reject) => {
        waiting.set(id, { resolve, reject });
        ws.send(JSON.stringify({ type: 'feedback', id, feedback }));
      });
    },
    hide() {
      live().send(JSON.stringify({ type: 'hide' }));
    },
    stop() {
      stopped = true;
      clearTimeout(retry);
      socket?.close();
    },
  };
}

/**
 * `url` when it can be dialled, else `undefined` with a warning: `new WebSocket`
 * throws on a malformed URL, and the jig is a convenience, so it switches off
 * rather than stop the server.
 */
function usableUrl(url: string | undefined, logger: { warn: (msg: string) => void }) {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'ws:' || parsed.protocol === 'wss:') return url;
  } catch {
    // Reported below.
  }
  logger.warn(`mael-jig: MAEL_JIG_URL is not a WebSocket URL (${url}), so the jig is off`);
  return undefined;
}

/** The worktree that holds `root`, or `null` outside a git checkout. */
function gitToplevel(root: string): string | null {
  try {
    return execFileSync('git', ['rev-parse', '--show-toplevel'], {
      cwd: root,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return null;
  }
}

function body(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let text = '';
    req.setEncoding('utf8');
    req.on('data', (chunk: string) => (text += chunk));
    req.on('end', () => resolve(text));
    req.on('error', reject);
  });
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function json(res: ServerResponse, status: number, value: unknown): void {
  res.statusCode = status;
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify(value));
}
