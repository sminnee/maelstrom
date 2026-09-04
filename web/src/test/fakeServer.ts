import type { ApiClient } from '../api/http';
import { createApiClient } from '../api/http';
import type { ChangeNotice } from '../api/types';
import type { EventSourceLike } from '../live/changeStream';
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
  /** Mutate the world, then send `notice` on every open stream. */
  change(notice: ChangeNotice, mutate?: (world: World) => void): void;
  refuse(route: RegExp, error: Refusal): void;
  /** Drop every open stream; `how` says whether the browser is still retrying. */
  dropStream(how?: 'connecting' | 'closed'): void;
  /** Open every stream that is not open yet, with `epoch`. */
  openStreams(epoch?: string): void;
}

export function createFakeServer(
  opts: { world?: World; transcripts?: Record<AgentId, Transcript>; autoOpen?: boolean } = {},
): FakeServer {
  const world = opts.world ?? emptyWorld();
  const transcripts = opts.transcripts ?? {};
  const autoOpen = opts.autoOpen ?? true;
  const requests: FakeRequest[] = [];
  const sources: FakeEventSource[] = [];
  const refusals: { route: RegExp; error: Refusal }[] = [];

  const fetchImpl: typeof fetch = async (input, init) => {
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
    return route(method, path, world, transcripts);
  };

  const api = createApiClient({ fetch: fetchImpl });

  const server: FakeServer = {
    world,
    transcripts,
    requests,
    sources,
    api,
    fetch: fetchImpl,
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

function notFound(what: string): Response {
  return json(404, { error: { code: 'unknown_id', message: `No ${what}` } });
}

const NOT_IMPLEMENTED = [
  /^POST \/api\/documents\/[^/]+\/comments/,
  /^POST \/api\/documents\/[^/]+\/(approve|request-changes)$/,
  /^POST \/api\/tasks$/,
  /^POST \/api\/shaping$/,
];

/** The server's routes, over the fake world. */
function route(
  method: string,
  path: string,
  world: World,
  transcripts: Record<AgentId, Transcript>,
): Response {
  const [pathname, query = ''] = path.split('?') as [string, string?];
  const params = new URLSearchParams(query);
  const key = `${method} ${pathname}`;
  if (NOT_IMPLEMENTED.some((r) => r.test(key))) {
    return json(501, {
      error: { code: 'not_implemented', message: `${key} is not implemented yet` },
    });
  }
  if (method === 'GET') {
    if (pathname === '/api/projects') return json(200, { projects: Object.values(world.projects) });
    if (pathname === '/api/worktrees')
      return json(200, { worktrees: Object.values(world.worktrees) });
    if (pathname === '/api/tasks') {
      const project = params.get('project');
      const tasks = Object.values(world.tasks)
        .filter((t) => !project || t.project === project)
        .map((task) => omit(task, 'content', 'log'));
      return json(200, { tasks, version: 'fake' });
    }
    let m = pathname.match(/^\/api\/tasks\/([^/]+)\/([^/]+)$/);
    if (m) {
      const task = world.tasks[`${m[1]}/${m[2]}`];
      return task ? json(200, task) : notFound(`task ${m[1]}/${m[2]}`);
    }
    if (pathname === '/api/agents') return json(200, { agents: Object.values(world.agents) });
    m = pathname.match(/^\/api\/agents\/([^/]+)$/);
    if (m) {
      const agent = world.agents[m[1]!];
      if (!agent) return notFound(`agent ${m[1]}`);
      const pendingRequest = agent.pendingRequestId
        ? (transcripts[agent.id]?.items.find(
            (i) => 'requestId' in i && i.requestId === agent.pendingRequestId,
          ) ?? null)
        : null;
      return json(200, { ...agent, pendingRequest });
    }
    if (pathname === '/api/attention') {
      const open = params.get('open');
      const items = Object.values(world.attention).filter((a) => !open || a.clearedAt === null);
      return json(200, { attention: items });
    }
    if (pathname === '/api/documents') {
      const documents = Object.values(world.documents).map((doc) => omit(doc, 'markdown'));
      return json(200, { documents });
    }
    m = pathname.match(/^\/api\/documents\/([^/]+)$/);
    if (m) {
      const doc = world.documents[m[1]!];
      return doc ? json(200, doc) : notFound(`document ${m[1]}`);
    }
    if (pathname === '/api/desk') return json(200, { desk: Object.values(world.desk) });
  }
  return json(404, { error: { code: 'unknown_id', message: `No route ${key}` } });
}
