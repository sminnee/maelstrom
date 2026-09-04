import type { ApiClient } from '../api/http';
import { createApiClient } from '../api/http';
import type { ChangeNotice } from '../api/types';
import type { EventSourceLike } from '../live/changeStream';
import type { Command, Reply as CommandReply } from '../protocol/commands';
import type { TaskEdit } from '../api/types';
import type { TaskStatus } from '../protocol/entities';
import { emptyWorld } from '../protocol/reducer';
import type { World } from '../protocol/events';
import type { AgentId } from '../protocol/ids';
import type { Transcript } from '../protocol/transcript';
import { FakeEventSource } from './fakeEventSource';

export interface Refusal {
  status: number;
  code: string;
  message?: string;
}

export interface FakeRequest {
  method: string;
  path: string;
  body: unknown;
}

/**
 * The orchestrator server, faked at the wire: a `fetch` that answers the
 * GET routes from `world`, and an `EventSource` factory whose sources open at
 * once. The test moves the world with `change`, which also sends the notice
 * the real server would; `refuse` makes a route fail; `dropStream` drops the
 * notice stream the way the browser reports a drop.
 */
export interface FakeServer {
  world: World;
  /** What each agent's transcript stream would carry; the agent detail reads its wait from here. */
  transcripts: Record<AgentId, Transcript>;
  requests: FakeRequest[];
  sources: FakeEventSource[];
  api: ApiClient;
  fetch: typeof fetch;
  eventSourceFactory: (url: string) => EventSourceLike;
  /** What a GET of `path` answers right now, parsed. Throws on a refusal. */
  read(path: string): unknown;
  /** Mutate the world, then send `notice` on every open stream. */
  change(notice: ChangeNotice, mutate?: (world: World) => void): void;
  refuse(route: RegExp, error: Refusal): void;
  /** Forget every refusal. */
  allow(): void;
  /** Hold every request until `release`, so a test can see the loading state. */
  hold(): void;
  release(): void;
  /** Drop every open stream; `how` says whether the browser is still retrying. */
  dropStream(how?: 'connecting' | 'closed'): void;
  /** Open every stream that is not open yet, with `epoch`. */
  openStreams(epoch?: string): void;
}

export interface FakeServerOptions {
  world?: World;
  transcripts?: Record<AgentId, Transcript>;
  autoOpen?: boolean;
  /**
   * Runs the command a POST amounts to. While the fake backend owns the world
   * this is its `command`, so the consequences arrive as its frames; without
   * one the fake applies a plain change to its own world and sends the notice.
   */
  command?: (cmd: Command) => Promise<CommandReply<Command>>;
}

export function createFakeServer(opts: FakeServerOptions = {}): FakeServer {
  const world = opts.world ?? emptyWorld();
  const transcripts = opts.transcripts ?? {};
  const autoOpen = opts.autoOpen ?? true;
  const requests: FakeRequest[] = [];
  const sources: FakeEventSource[] = [];
  const refusals: { route: RegExp; error: Refusal }[] = [];
  let held: Promise<void> | null = null;
  let release: (() => void) | null = null;

  const fetchImpl: typeof fetch = async (input, init) => {
    if (held) await held;
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? 'GET';
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    requests.push({ method, path, body });
    const refusal = refusals.find((r) => r.route.test(`${method} ${path}`));
    if (refusal) {
      const { status, code, message } = refusal.error;
      return json(status, { error: { code, message: message ?? code } });
    }
    if (method === 'GET') {
      const reply = route(method, path, server.world, server.transcripts);
      return json(reply.status, reply.body);
    }
    const reply = await command(method, path, body, server, opts.command);
    return json(reply.status, reply.body);
  };

  const api = createApiClient({ fetch: fetchImpl });

  const server: FakeServer = {
    world,
    transcripts,
    requests,
    sources,
    api,
    fetch: fetchImpl,
    read(path) {
      const reply = route('GET', path, server.world, server.transcripts);
      if (reply.status >= 400) throw new Error(`GET ${path}: ${reply.status}`);
      return reply.body;
    },
    eventSourceFactory: (url) => {
      const source = new FakeEventSource(url);
      sources.push(source);
      // Deferred: the stream assigns its handlers after it has the source.
      if (autoOpen) queueMicrotask(() => source.readyState === 0 && source.open());
      return source;
    },
    change(notice, mutate) {
      mutate?.(world);
      for (const source of sources) {
        if (source.readyState === FakeEventSource.OPEN) source.emit('change', notice);
      }
    },
    refuse(route, error) {
      refusals.push({ route, error });
    },
    allow() {
      refusals.length = 0;
    },
    hold() {
      if (held) return;
      held = new Promise<void>((resolve) => {
        release = resolve;
      });
    },
    release() {
      release?.();
      held = null;
      release = null;
    },
    dropStream(how = 'connecting') {
      for (const source of sources) {
        if (source.readyState === FakeEventSource.OPEN) source.fail(how);
      }
    },
    openStreams(epoch = 'e1') {
      for (const source of sources) {
        if (source.readyState !== FakeEventSource.OPEN) source.open(epoch);
      }
    },
  };
  return server;
}

function omit<T extends object>(value: T, ...names: (keyof T)[]): Partial<T> {
  const copy: Partial<T> = { ...value };
  for (const name of names) delete copy[name];
  return copy;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

interface Reply {
  status: number;
  body: unknown;
}

const ok = (body: unknown): Reply => ({ status: 200, body });
const error = (status: number, code: string, message: string): Reply => ({
  status,
  body: { error: { code, message } },
});
const notFound = (what: string): Reply => error(404, 'unknown_id', `No ${what}`);

const NOT_IMPLEMENTED = [
  /^POST \/api\/documents\/[^/]+\/comments/,
  /^POST \/api\/documents\/[^/]+\/(approve|request-changes)$/,
  /^POST \/api\/tasks$/,
  /^POST \/api\/shaping$/,
];

/** The HTTP status for each error code, as the server maps them. */
const STATUS_FOR_CODE: Record<string, number> = {
  unknown_id: 404,
  invalid: 400,
  agent_exited: 409,
  not_waiting: 409,
  stale_request: 409,
  wrong_wait_kind: 409,
  stale_version: 409,
  not_implemented: 501,
};

/** The command a POST, PATCH or DELETE amounts to, or null for no such route. */
function commandFor(method: string, path: string, body: unknown): Command | null {
  const b = (body ?? {}) as Record<string, unknown>;
  const str = (name: string) => (b[name] === undefined ? undefined : String(b[name]));
  let m = path.match(/^\/api\/agents\/([^/]+)\/(approve|deny|answer|say|stop|resume)$/);
  if (m && method === 'POST') {
    const agentId = m[1]!;
    switch (m[2]) {
      case 'approve':
        return { type: 'agent.approve', agentId, requestId: str('requestId') ?? '' };
      case 'deny':
        return {
          type: 'agent.deny',
          agentId,
          requestId: str('requestId') ?? '',
          reason: str('reason') ?? '',
        };
      case 'answer':
        return {
          type: 'agent.answer',
          agentId,
          requestId: str('requestId') ?? '',
          answers: (b.answers ?? {}) as Record<string, string>,
        };
      case 'say':
        return { type: 'agent.say', agentId, text: str('text') ?? '' };
      case 'stop':
        return { type: 'agent.stop', agentId };
      default:
        return { type: 'agent.resume', agentId, text: str('text') };
    }
  }
  m = path.match(/^\/api\/tasks\/(.+)\/launch$/);
  if (m && method === 'POST') return { type: 'agent.launch', taskId: m[1]!, model: str('model') };
  m = path.match(/^\/api\/tasks\/(.+)\/status$/);
  if (m && method === 'POST') {
    return { type: 'task.setStatus', taskId: m[1]!, status: str('status') as TaskStatus };
  }
  m = path.match(/^\/api\/tasks\/(.+)$/);
  if (m && method === 'PATCH') return { type: 'task.update', taskId: m[1]!, fields: b as TaskEdit };
  if (path === '/api/desk' && method === 'POST') return { type: 'desk.add', id: str('id') ?? '' };
  m = path.match(/^\/api\/desk\/(.+)$/);
  if (m && method === 'DELETE') return { type: 'desk.remove', id: decodeURIComponent(m[1]!) };
  return null;
}

/** A command route: refused, delegated, or applied to the fake world. */
async function command(
  method: string,
  path: string,
  body: unknown,
  server: FakeServer,
  run: FakeServerOptions['command'],
): Promise<Reply> {
  const [pathname] = path.split('?') as [string];
  const key = `${method} ${pathname}`;
  if (NOT_IMPLEMENTED.some((r) => r.test(key))) {
    return error(501, 'not_implemented', `${key} is not implemented yet`);
  }
  const cmd = commandFor(method, pathname, body);
  if (!cmd) return error(404, 'unknown_id', `No route ${key}`);
  if (run) {
    const reply = await run(cmd);
    if (reply.ok) return ok(reply.result);
    return error(STATUS_FOR_CODE[reply.error.code] ?? 400, reply.error.code, reply.error.message);
  }
  return applyToWorld(cmd, server);
}

/** The plain change a command makes to the fake world, and the notice it raises. */
function applyToWorld(cmd: Command, server: FakeServer): Reply {
  const { world } = server;
  switch (cmd.type) {
    case 'desk.add': {
      world.desk[cmd.id] = { id: cmd.id, addedAt: new Date().toISOString() };
      server.change({ kind: 'desk', ids: [cmd.id] });
      return ok({});
    }
    case 'desk.remove': {
      if (!(cmd.id in world.desk)) return error(404, 'unknown_id', `${cmd.id} is not on the desk`);
      delete world.desk[cmd.id];
      server.change({ kind: 'desk', ids: [cmd.id] });
      return ok({});
    }
    case 'task.setStatus':
    case 'task.update': {
      const task = world.tasks[cmd.taskId];
      if (!task) return error(404, 'unknown_id', `No task ${cmd.taskId}`);
      world.tasks[cmd.taskId] =
        cmd.type === 'task.setStatus'
          ? { ...task, status: cmd.status }
          : { ...task, ...cmd.fields };
      server.change({ kind: 'task', ids: [cmd.taskId] });
      return ok({});
    }
    case 'agent.launch': {
      if (!world.tasks[cmd.taskId]) return error(404, 'unknown_id', `No task ${cmd.taskId}`);
      return ok({ agentId: 'new1' });
    }
    default: {
      if ('agentId' in cmd && !world.agents[cmd.agentId]) {
        return error(404, 'unknown_id', `No agent ${cmd.agentId}`);
      }
      return ok({});
    }
  }
}

/** The server's routes, over the fake world. */
function route(
  method: string,
  path: string,
  world: World,
  transcripts: Record<AgentId, Transcript>,
): Reply {
  const [pathname, query = ''] = path.split('?') as [string, string?];
  const params = new URLSearchParams(query);
  const key = `${method} ${pathname}`;
  if (NOT_IMPLEMENTED.some((r) => r.test(key))) {
    return error(501, 'not_implemented', `${key} is not implemented yet`);
  }
  if (method === 'GET') {
    if (pathname === '/api/projects') return ok({ projects: Object.values(world.projects) });
    if (pathname === '/api/worktrees') return ok({ worktrees: Object.values(world.worktrees) });
    if (pathname === '/api/tasks') {
      const project = params.get('project');
      const tasks = Object.values(world.tasks)
        .filter((t) => !project || t.project === project)
        .map((task) => omit(task, 'content', 'log'));
      return ok({ tasks, version: 'fake' });
    }
    // The wire id is `<project>/<notebookId>`, two segments; the seed world's
    // bare ids are one. Either way the rest of the path is the id.
    let m = pathname.match(/^\/api\/tasks\/(.+)$/);
    if (m) {
      const task = world.tasks[m[1]!];
      return task ? ok(task) : notFound(`task ${m[1]}`);
    }
    if (pathname === '/api/agents') return ok({ agents: Object.values(world.agents) });
    m = pathname.match(/^\/api\/agents\/([^/]+)$/);
    if (m) {
      const agent = world.agents[m[1]!];
      if (!agent) return notFound(`agent ${m[1]}`);
      const pendingRequest = agent.pendingRequestId
        ? (transcripts[agent.id]?.items.find(
            (i) => 'requestId' in i && i.requestId === agent.pendingRequestId,
          ) ?? null)
        : null;
      return ok({ ...agent, pendingRequest });
    }
    if (pathname === '/api/attention') {
      const open = params.get('open');
      const items = Object.values(world.attention).filter((a) => !open || a.clearedAt === null);
      return ok({ attention: items });
    }
    if (pathname === '/api/documents') {
      const documents = Object.values(world.documents).map((doc) => omit(doc, 'markdown'));
      return ok({ documents });
    }
    m = pathname.match(/^\/api\/documents\/([^/]+)$/);
    if (m) {
      const doc = world.documents[m[1]!];
      return doc ? ok(doc) : notFound(`document ${m[1]}`);
    }
    if (pathname === '/api/desk') return ok({ desk: Object.values(world.desk) });
  }
  return error(404, 'unknown_id', `No route ${key}`);
}
