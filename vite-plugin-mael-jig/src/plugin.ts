// The jig's dev-server half. See CONTEXT.md, "Jig", and
// docs/dev/orchestrator-ui.md, "The jig".
//
// The jig is a **Live feedback** client: it exposes one param, `jig`, at
// `MAEL_FEEDBACK_URL`. The protocol is docs/dev/live-feedback.md.
//
// The jig is shown only while the socket is open and its `jig` param is on.
import { execFileSync } from 'node:child_process';
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { basename, dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Plugin } from 'vite';

interface Options {
  /**
   * The live feedback provider's WebSocket URL. Empty, the jig is off.
   * Defaults to `MAEL_FEEDBACK_URL`, which `mael env start` sets.
   */
  providerUrl?: string;
}

interface Reply {
  type: 'reply';
  id: number;
  ok: boolean;
  body: unknown;
}

/** The custom HMR event that carries a draft file to every page. */
const DRAFT_EVENT = 'mael-jig:draft';
/** The custom HMR event that tells every page whether to draw the jig. */
const STATE_EVENT = 'mael-jig:state';
const BACKOFF_MS = [250, 1000, 5000];
const CLIENT_HEADER = 'x-mael-jig-client';
const SETTLE_MS = 80;
/** The screenshots `.drafts/jig/` keeps. An agent reads the newest; the rest is history. */
const KEEP_SHOTS = 20;
const JPEG_DATA_URL = 'data:image/jpeg;base64,';
/** The params the jig exposes. Its one param shows the jig. */
const PARAMS = {
  jig: { label: 'Jig', type: 'boolean', description: 'Show the jig on each page' },
};
/**
 * The files in `.drafts/` the jig serves, and whether a page may write each.
 * The agent writes the control set, so the jig only reads it.
 */
const DRAFTS: Record<string, { writable: boolean }> = {
  'monkeypatch.css': { writable: true },
  'jig-values.json': { writable: true },
  'jig-controls.json': { writable: false },
};
/** The module each page loads. Virtual, not a `/@fs/` path, so a linked copy serves: see docs/dev/orchestrator-ui.md, "The jig". */
const CLIENT_ID = 'virtual:mael-jig/client';
const SELF = fileURLToPath(import.meta.url);
/** The overlay beside this file: `.ts` in the source, `.js` in the build. */
const OVERLAY = join(dirname(SELF), `overlay${extname(SELF)}`);

export function maelJig({ providerUrl = process.env.MAEL_FEEDBACK_URL }: Options = {}): Plugin {
  // vitest serves no page, so a consumer's config needs no guard.
  if (process.env.VITEST) return { name: 'mael-jig' };
  // Empty until the server starts, and for good when it runs outside a git
  // checkout: the jig is a convenience, so it switches off rather than stop the server.
  let drafts = '';
  // The last PUT to each file, so a watcher event it caused names its page.
  const lastWrites = new Map<string, { text: string; from: string }>();
  let base = '/';
  let stopProvider = () => {};

  /** The draft file name of `path`, or `undefined` when the jig does not serve it. */
  const draftName = (path: string) => {
    if (!drafts || dirname(path) !== drafts) return undefined;
    const name = basename(path);
    return Object.hasOwn(DRAFTS, name) ? name : undefined;
  };
  const read = (name: string) => {
    try {
      return readFileSync(join(drafts, name), 'utf8');
    } catch {
      return '';
    }
  };

  return {
    name: 'mael-jig',
    apply: 'serve',
    // The overlay's one dependency is plain ESM. Pre-bundled, it would be
    // looked up from the consumer's root, where pnpm does not hoist it.
    config() {
      return { optimizeDeps: { exclude: ['modern-screenshot'] } };
    },
    configResolved(config) {
      base = config.base;
    },
    resolveId(id) {
      if (id === CLIENT_ID) return `\0${CLIENT_ID}`;
    },
    load(id) {
      if (id !== `\0${CLIENT_ID}`) return;
      return [
        `import { mountJig } from ${JSON.stringify(OVERLAY)};`,
        'if (import.meta.hot) void mountJig({ hot: import.meta.hot });',
      ].join('\n');
    },
    configureServer(server) {
      const worktree = gitToplevel(server.config.root);
      if (!worktree) {
        server.config.logger.warn('mael-jig: not in a git checkout, so the jig is off');
        return;
      }
      drafts = join(worktree, '.drafts');
      // The directory, not the file: a file that does not exist yet is not watched.
      mkdirSync(drafts, { recursive: true });
      server.watcher.add(drafts);
      // Read a moment after the last event, not at it. A write truncates the file
      // first, so an event can see it empty, and the watcher drops a second
      // `change` that follows within 50 ms: the pages would keep the empty read.
      const settles = new Map<string, ReturnType<typeof setTimeout>>();
      const broadcast = (changed: string) => {
        const name = draftName(changed);
        if (!name) return;
        clearTimeout(settles.get(name));
        settles.set(
          name,
          setTimeout(() => {
            const text = read(name);
            const last = lastWrites.get(name);
            const from = last?.text === text ? last.from : null;
            server.ws.send({ type: 'custom', event: DRAFT_EVENT, data: { name, text, from } });
          }, SETTLE_MS),
        );
      };
      server.watcher.on('add', broadcast);
      server.watcher.on('change', broadcast);
      server.watcher.on('unlink', broadcast);

      const provider = connect(usableUrl(providerUrl, server.config.logger), (visible) =>
        server.ws.send({ type: 'custom', event: STATE_EVENT, data: { visible } }),
      );
      const shots = join(drafts, 'jig');
      stopProvider = provider.stop;

      // Before the routes: a page on another origin must not write a draft or reach the agent.
      server.middlewares.use('/__mael', (req, res, next) => {
        const refusal = foreignRequest(req);
        if (refusal) json(res, 403, { error: { message: refusal } });
        else next();
      });

      // Mounted under the prefix, so `req.url` is the rest: `/<name>`, still encoded.
      server.middlewares.use('/__mael/drafts', (req, res) => {
        const name = (req.url ?? '').replace(/^\//, '').split('?')[0]!;
        const draft = Object.hasOwn(DRAFTS, name) ? DRAFTS[name] : undefined;
        if (!draft) {
          json(res, 404, { error: { message: `The jig serves no draft ${name}` } });
          return;
        }
        if (req.method === 'GET') {
          res.setHeader('content-type', 'text/plain; charset=utf-8');
          res.end(read(name));
          return;
        }
        if (req.method !== 'PUT') {
          json(res, 405, { error: { message: `${req.method} is not allowed` } });
          return;
        }
        if (!draft.writable) {
          json(res, 405, { error: { message: `${name} is read only` } });
          return;
        }
        void body(req)
          .then((text) => {
            lastWrites.set(name, { text, from: String(req.headers[CLIENT_HEADER] ?? '') });
            // Again on each write: `.drafts/` can be cleaned away under a running server.
            mkdirSync(drafts, { recursive: true });
            writeFileSync(join(drafts, name), text);
            res.statusCode = 204;
            res.end();
          })
          .catch((error: unknown) => json(res, 500, { error: { message: String(error) } }));
      });

      server.middlewares.use('/__mael/state', (req, res) => {
        if (req.method !== 'GET') {
          json(res, 405, { error: { message: `${req.method} is not allowed` } });
          return;
        }
        json(res, 200, { visible: provider.visible() });
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
              .then((sent) => withShotFile(sent, shots))
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
    // Vite runs this as the server closes, in middleware mode too, which has no httpServer.
    closeBundle() {
      stopProvider();
    },
    // A draft is not a module, so nothing reloads for it: the event above is the update.
    handleHotUpdate({ file: changed }) {
      if (draftName(changed)) return [];
    },
    transformIndexHtml() {
      if (!drafts) return;
      // Vite's URL for a `\0` module id. Vite does not add the base to a plugin's tag.
      const src = `${base}@id/__x00__${CLIENT_ID}`;
      return [{ tag: 'script', attrs: { type: 'module', src }, injectTo: 'body' }];
    },
  };
}

/**
 * The socket to the provider, kept open: it reconnects with backoff, and
 * reports the jig hidden while it is down. `onState` hears each change.
 */
function connect(url: string | undefined, onState: (visible: boolean) => void) {
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
      ws.send(JSON.stringify({ type: 'exposeParams', params: PARAMS }));
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
      if (frame.type === 'setParams' && typeof frame.params?.jig === 'boolean') {
        show(frame.params.jig);
      }
      if (frame.type === 'reply') {
        waiting.get(frame.id)?.resolve(frame);
        waiting.delete(frame.id);
      }
    };
    ws.onclose = () => {
      socket = null;
      show(false);
      for (const { reject } of waiting.values())
        reject(new Error('The live feedback provider went away'));
      waiting.clear();
      if (stopped) return;
      retry = setTimeout(open, BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)]);
    };
  };

  /** The open socket, or a throw that says why there is none. */
  const live = () => {
    if (socket?.readyState !== WebSocket.OPEN) {
      throw new Error(
        url ? 'The live feedback provider is not reachable' : 'No live feedback provider is set',
      );
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
      live().send(JSON.stringify({ type: 'setParams', params: { jig: false } }));
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
  logger.warn(`mael-jig: MAEL_FEEDBACK_URL is not a WebSocket URL (${url}), so the jig is off`);
  return undefined;
}

/**
 * `feedback` with its screenshot written to `dir` and named by file. A
 * screenshot that is not a JPEG data URL, or that will not write, is dropped,
 * and the feedback goes without it.
 */
function withShotFile(feedback: unknown, dir: string): unknown {
  if (typeof feedback !== 'object' || feedback === null || !('screenshot' in feedback)) {
    return feedback;
  }
  const { screenshot, ...rest } = feedback as { screenshot: unknown };
  if (typeof screenshot !== 'string' || !screenshot.startsWith(JPEG_DATA_URL)) return rest;
  const name = `shot-${Date.now()}.jpg`;
  try {
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, name), Buffer.from(screenshot.slice(JPEG_DATA_URL.length), 'base64'));
    pruneShots(dir);
  } catch {
    return rest;
  }
  return { ...rest, screenshot: name };
}

/** Delete all but the newest `KEEP_SHOTS` screenshots in `dir`, by the time in the name. */
function pruneShots(dir: string): void {
  const stamp = (name: string) => Number(/^shot-(\d+)\.jpg$/.exec(name)?.[1]);
  const shots = readdirSync(dir)
    .filter((name) => !Number.isNaN(stamp(name)))
    .sort((a, b) => stamp(b) - stamp(a));
  for (const old of shots.slice(KEEP_SHOTS)) rmSync(join(dir, old), { force: true });
}

/**
 * Why a state-changing request did not come from the overlay, or `undefined`
 * when it may have. The jig header makes a cross-origin request preflight,
 * and Vite's CORS lets other localhost origins through a preflight, so
 * `Origin` must name this server too. A request with no `Origin` is not from
 * a browser page: any host that reaches the server is trusted.
 */
function foreignRequest(req: IncomingMessage): string | undefined {
  if (req.method === 'GET' || req.method === 'HEAD') return undefined;
  if (!req.headers[CLIENT_HEADER]) return `A ${req.method} needs the ${CLIENT_HEADER} header`;
  const from = req.headers.origin;
  // HTTP/2, which Vite serves over HTTPS, names the host in `:authority` and sends no `Host`.
  const host = req.headers.host ?? req.headers[':authority'];
  if (from === undefined) return undefined;
  const fromHost = originHost(from);
  if (fromHost !== undefined && fromHost === host) return undefined;
  return `A ${req.method} from ${from} is refused`;
}

/** The `host[:port]` of an `Origin` value, or `undefined` for `null` and junk. */
function originHost(origin: string): string | undefined {
  try {
    return new URL(origin).host || undefined;
  } catch {
    return undefined;
  }
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
