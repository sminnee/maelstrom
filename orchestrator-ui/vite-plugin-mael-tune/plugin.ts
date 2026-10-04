// The tuning jig's dev-server half. See CONTEXT.md, "Jig", and
// docs/dev/orchestrator-ui.md, "The tuning jig".
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Plugin } from 'vite';

interface Options {
  /** The orchestrator server that Send posts to. Unset, Send is off and live CSS still works. */
  orchestratorUrl?: string;
}

/** The custom HMR event that carries the file to every page. */
const UPDATE_EVENT = 'mael-tune:update';
const CLIENT_HEADER = 'x-mael-tune-client';
const SETTLE_MS = 80;
const CLIENT = join(dirname(fileURLToPath(import.meta.url)), 'client.ts');

export function maelTune({ orchestratorUrl }: Options): Plugin {
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
    name: 'mael-tune',
    apply: 'serve',
    configureServer(server) {
      const worktree = gitToplevel(server.config.root);
      if (!worktree) {
        server.config.logger.warn('mael-tune: not in a git checkout, so the jig is off');
        return;
      }
      const drafts = join(worktree, '.drafts');
      file = join(drafts, 'tuning.css');
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

      server.middlewares.use('/__mael/tuning', (req, res) => {
        if (req.method === 'GET') {
          json(res, 200, { css: read(), canSend: Boolean(orchestratorUrl) });
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

      server.middlewares.use('/__mael/send', (req, res) => {
        if (req.method !== 'POST') {
          json(res, 405, { error: { message: `${req.method} is not allowed` } });
          return;
        }
        if (!orchestratorUrl) {
          json(res, 503, { error: { message: 'No orchestrator is set for this dev server' } });
          return;
        }
        void body(req)
          .then((sent) => send(orchestratorUrl, worktree, sent))
          .then(
            ({ status, reply }) => json(res, status, reply),
            (error: unknown) => json(res, 502, { error: { message: String(error) } }),
          );
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

/** Post the CSS to the orchestrator, for the worktree it lists at this path. */
async function send(
  orchestratorUrl: string,
  worktree: string,
  sent: string,
): Promise<{ status: number; reply: unknown }> {
  const answered = await fetch(`${orchestratorUrl}/api/worktrees`);
  if (!answered.ok) return { status: answered.status, reply: await answered.json() };
  const listed = (await answered.json()) as { worktrees: { id: string; path: string }[] };
  const match = listed.worktrees.find((w) => samePath(w.path, worktree));
  if (!match) {
    return {
      status: 404,
      reply: { error: { message: `The orchestrator has no worktree at ${worktree}` } },
    };
  }
  const posted = await fetch(`${orchestratorUrl}/api/worktrees/${match.id}/tuning`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: sent,
  });
  return { status: posted.status, reply: await posted.json() };
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

function samePath(a: string, b: string): boolean {
  try {
    return realpathSync(a) === realpathSync(b);
  } catch {
    return false;
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

function json(res: ServerResponse, status: number, value: unknown): void {
  res.statusCode = status;
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify(value));
}
