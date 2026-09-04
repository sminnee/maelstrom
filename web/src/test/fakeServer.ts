import type { ApiClient } from '../api/http';
import { createApiClient } from '../api/http';
import type { ChangeNotice, TaskEdit } from '../api/types';
import type { EventSourceLike } from '../live/changeStream';
import type { PermissionMode } from '../protocol/modes';
import { MODES } from '../protocol/modes';
import type { SocketLike } from '../live/socketLike';
import type { TranscriptEvent } from '../live/transcriptReducer';
import type { Attention } from '../protocol/attention';
import type { Document } from '../protocol/documents';
import type {
  Agent,
  DeskEntry,
  Project,
  Task,
  TaskMode,
  TaskStatus,
  Worktree,
} from '../protocol/entities';
import type { AgentId, TaskId } from '../protocol/ids';
import type { Transcript, TranscriptItem } from '../protocol/transcript';
import { FakeEventSource } from './fakeEventSource';
import { FakeSocket } from './fakeSocket';

/**
 * The world the fake serves: the seven tables, keyed by id, with tasks and
 * documents whole so the detail routes have their prose.
 */
export interface FakeWorld {
  projects: Record<string, Project>;
  worktrees: Record<string, Worktree>;
  tasks: Record<string, Task>;
  agents: Record<AgentId, Agent>;
  documents: Record<string, Document>;
  attention: Record<string, Attention>;
  desk: Record<string, DeskEntry>;
}

export function emptyFakeWorld(): FakeWorld {
  return {
    projects: {},
    worktrees: {},
    tasks: {},
    agents: {},
    documents: {},
    attention: {},
    desk: {},
  };
}

export interface Refusal {
  status: number;
  code: string;
  message?: string;
  /** Fields the refusal carries beside its code and message, as the server's do. */
  [field: string]: unknown;
}

export interface FakeRequest {
  method: string;
  path: string;
  body: unknown;
}

/**
 * The orchestrator server, faked at the wire: a `fetch` that answers the
 * routes from `world`, an `EventSource` factory whose sources open at once,
 * and a `WebSocket` factory whose sockets open with a transcript snapshot.
 * A command changes the world the way the server would and sends the
 * notices; the test moves the world itself with `change`, `append` and
 * `patch`; `refuse` makes a route fail; `dropStream` and `dropSockets` drop
 * connections the way the browser reports a drop.
 */
export interface FakeServer {
  world: FakeWorld;
  transcripts: Record<AgentId, Transcript>;
  requests: FakeRequest[];
  sources: FakeEventSource[];
  /** Every transcript socket opened so far, closed ones included. */
  sockets: FakeSocket[];
  api: ApiClient;
  fetch: typeof fetch;
  eventSourceFactory: (url: string) => EventSourceLike;
  webSocketFactory: (path: string) => SocketLike;
  /** What a GET of `path` answers right now, parsed. Throws on a refusal. */
  read(path: string): unknown;
  /** Mutate the world, then send `notice` on every open stream. */
  change(notice: ChangeNotice, mutate?: (world: FakeWorld) => void): void;
  /** Add an item to an agent's transcript and send the frame on its open sockets. */
  append(agentId: AgentId, item: TranscriptItem): void;
  /** Patch an item and send the frame. */
  patch(agentId: AgentId, itemId: string, patch: Partial<TranscriptItem>): void;
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
  /** Drop every open socket on `agentId` (every agent with none), as a network drop would. */
  dropSockets(agentId?: AgentId): void;
}

export interface FakeServerOptions {
  world?: FakeWorld;
  transcripts?: Record<AgentId, Transcript>;
  autoOpen?: boolean;
}

export function createFakeServer(opts: FakeServerOptions = {}): FakeServer {
  const autoOpen = opts.autoOpen ?? true;
  const requests: FakeRequest[] = [];
  const sources: FakeEventSource[] = [];
  const sockets: FakeSocket[] = [];
  const seqs: Record<AgentId, number> = {};
  /** Every frame sent per agent, so a socket that comes back with a cursor gets a replay. */
  const frames: Record<AgentId, { seq: number; event: TranscriptEvent }[]> = {};
  const refusals: { route: RegExp; error: Refusal }[] = [];
  let held: Promise<void> | null = null;
  let release: (() => void) | null = null;
  let nextId = 1;

  const openSockets = (agentId: AgentId) =>
    sockets.filter((socket) => socket.agentId === agentId && !socket.closed && socket.opened);
  const transcriptOf = (agentId: AgentId): Transcript =>
    server.transcripts[agentId] ?? { agentId, items: [], truncatedBefore: false };

  const fetchImpl: typeof fetch = async (input, init) => {
    if (held) await held;
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? 'GET';
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    requests.push({ method, path, body });
    const refusal = refusals.find((r) => r.route.test(`${method} ${path}`));
    if (refusal) {
      const { status, code, message, ...detail } = refusal.error;
      return json(status, { error: { code, message: message ?? code, ...detail } });
    }
    const reply =
      method === 'GET' ? read(path, server) : command(method, path, body, server, () => nextId++);
    return json(reply.status, reply.body);
  };

  const emitTranscript = (event: TranscriptEvent) => {
    const seq = (seqs[event.agentId] = (seqs[event.agentId] ?? 0) + 1);
    (frames[event.agentId] ??= []).push({ seq, event });
    for (const socket of openSockets(event.agentId)) socket.receive({ seq, event });
  };

  const server: FakeServer = {
    world: opts.world ?? emptyFakeWorld(),
    transcripts: opts.transcripts ?? {},
    requests,
    sources,
    sockets,
    api: createApiClient({ fetch: fetchImpl }),
    fetch: fetchImpl,
    read(path) {
      const reply = read(path, server);
      if (reply.status >= 400) throw new Error(`GET ${path}: ${reply.status}`);
      return reply.body;
    },
    eventSourceFactory: (url) => {
      const source = new FakeEventSource(url);
      sources.push(source);
      // Deferred: the stream assigns its handlers after it has the source.
      if (autoOpen) {
        queueMicrotask(() => source.readyState === FakeEventSource.CONNECTING && source.open());
      }
      return source;
    },
    webSocketFactory: (path) => {
      const socket = new FakeSocket(path);
      sockets.push(socket);
      queueMicrotask(() => {
        if (socket.closed) return;
        const agentId = socket.agentId;
        if (!server.world.agents[agentId]) {
          socket.serverClose(4404);
          return;
        }
        socket.opened = true;
        socket.open();
        const seq = seqs[agentId] ?? 0;
        const from = socket.from;
        // As the server does: a cursor inside what was sent replays the rest,
        // anything else gets the snapshot.
        if (from !== null && from <= seq) {
          socket.receive({
            type: 'transcript.replay',
            seq,
            frames: (frames[agentId] ?? []).filter((f) => f.seq > from),
          });
          return;
        }
        const transcript = transcriptOf(agentId);
        socket.receive({
          type: 'transcript.snapshot',
          seq,
          items: transcript.items,
          truncatedBefore: transcript.truncatedBefore,
        });
      });
      return socket;
    },
    change(notice, mutate) {
      mutate?.(server.world);
      for (const source of sources) {
        if (source.readyState === FakeEventSource.OPEN) source.emit('change', notice);
      }
    },
    append(agentId, item) {
      const transcript = transcriptOf(agentId);
      server.transcripts[agentId] = { ...transcript, items: [...transcript.items, item] };
      emitTranscript({ type: 'transcript.append', agentId, item });
    },
    patch(agentId, itemId, patch) {
      const transcript = transcriptOf(agentId);
      server.transcripts[agentId] = {
        ...transcript,
        items: transcript.items.map((i) =>
          i.id === itemId ? ({ ...i, ...patch } as TranscriptItem) : i,
        ),
      };
      emitTranscript({ type: 'transcript.update', agentId, itemId, patch });
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
    dropSockets(agentId) {
      for (const socket of sockets) {
        if (!socket.closed && (agentId === undefined || socket.agentId === agentId)) {
          socket.serverClose(1006);
        }
      }
    },
  };
  return server;
}

// -- replies --

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

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function omit<T extends object>(value: T, ...names: (keyof T)[]): Partial<T> {
  const copy: Partial<T> = { ...value };
  for (const name of names) delete copy[name];
  return copy;
}

// -- reads --

function read(path: string, server: FakeServer): Reply {
  const { world, transcripts } = server;
  const [pathname, query = ''] = path.split('?') as [string, string?];
  const params = new URLSearchParams(query);
  if (pathname === '/api/projects') return ok({ projects: Object.values(world.projects) });
  if (pathname === '/api/worktrees') return ok({ worktrees: Object.values(world.worktrees) });
  if (pathname === '/api/tasks') {
    const tasks = Object.values(world.tasks).map((task) => omit(task, 'content', 'log'));
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
  m = pathname.match(/^\/api\/agents\/([^/]+)\/transcript$/);
  if (m) {
    const agentId = m[1]!;
    if (!world.agents[agentId]) return notFound(`agent ${agentId}`);
    const transcript = transcripts[agentId] ?? { items: [], truncatedBefore: false };
    return ok({ agentId, items: transcript.items, truncatedBefore: transcript.truncatedBefore });
  }
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
    return ok({ documents: Object.values(world.documents).map((doc) => omit(doc, 'markdown')) });
  }
  m = pathname.match(/^\/api\/documents\/([^/]+)$/);
  if (m) {
    const doc = world.documents[m[1]!];
    return doc ? ok(doc) : notFound(`document ${m[1]}`);
  }
  if (pathname === '/api/desk') return ok({ desk: Object.values(world.desk) });
  return error(404, 'unknown_id', `No route GET ${pathname}`);
}

// -- commands --

const NOT_IMPLEMENTED = [
  /^POST \/api\/documents\/[^/]+\/comments/,
  /^POST \/api\/documents\/[^/]+\/(approve|request-changes)$/,
  /^POST \/api\/shaping$/,
];

/**
 * A command route, with the consequences the real server's world would
 * show: the notices it raises, the agent it moves, the item it patches.
 */
function command(
  method: string,
  path: string,
  body: unknown,
  server: FakeServer,
  mint: () => number,
): Reply {
  const [pathname] = path.split('?') as [string];
  const key = `${method} ${pathname}`;
  if (NOT_IMPLEMENTED.some((r) => r.test(key))) {
    return error(501, 'not_implemented', `${key} is not implemented yet`);
  }
  const b = (body ?? {}) as Record<string, unknown>;
  const str = (name: string) => (b[name] === undefined ? undefined : String(b[name]));
  const { world } = server;
  const now = () => new Date().toISOString();

  let m = pathname.match(
    /^\/api\/agents\/([^/]+)\/(approve|deny|answer|say|stop|resume|set-mode)$/,
  );
  if (m && method === 'POST') {
    const agentId = m[1]!;
    const agent = world.agents[agentId];
    if (!agent) return notFound(`agent ${agentId}`);
    const action = m[2]!;
    // A subagent is read, never driven: the refusal names the parent to drive.
    if (agent.parent) {
      return error(
        400,
        'invalid',
        `${agentId} is a subagent of ${agent.parent}; drive ${agent.parent}`,
      );
    }
    if (action === 'resume') {
      if (agent.state !== 'exited') return error(400, 'invalid', `Agent ${agentId} is running`);
      world.agents[agentId] = { ...agent, state: 'idle', exitCode: null };
      server.change({ kind: 'agent', ids: [agentId] });
      return ok({});
    }
    if (agent.state === 'exited') return error(409, 'agent_exited', `Agent ${agentId} has exited`);
    if (action === 'say') {
      const text = str('text')?.trim() ?? '';
      if (!text) return error(400, 'invalid', 'Message is empty');
      server.append(agentId, {
        id: `m${mint()}`,
        ts: now(),
        type: 'message',
        role: 'user',
        markdown: text,
      });
      return ok({});
    }
    if (action === 'set-mode') {
      const mode = MODES.find((m) => m === str('mode'));
      if (!mode) return error(400, 'invalid', `Unknown mode: ${str('mode') ?? ''}`);
      // The real child announces the new mode itself; the fake does it here.
      world.agents[agentId] = { ...agent, permissionMode: mode };
      server.change({ kind: 'agent', ids: [agentId] });
      return ok({});
    }
    if (action === 'stop') {
      world.agents[agentId] = { ...agent, state: 'exited', exitCode: 0, pendingRequestId: null };
      server.change({ kind: 'agent', ids: [agentId] });
      return ok({});
    }
    // approve, deny, answer: one wait, answered.
    const requestId = str('requestId');
    if (!agent.pendingRequestId)
      return error(409, 'not_waiting', `Agent ${agentId} is not waiting`);
    if (requestId !== agent.pendingRequestId) {
      return error(409, 'stale_request', `Request ${requestId} is no longer pending`);
    }
    if (action === 'deny' && !str('reason')?.trim()) {
      return error(400, 'invalid', 'A reason is required');
    }
    const wait = server.transcripts[agentId]?.items.find(
      (i) => 'requestId' in i && i.requestId === requestId,
    );
    if (wait) {
      const patch: Partial<TranscriptItem> =
        action === 'answer'
          ? { answers: (b.answers ?? {}) as Record<string, string> }
          : action === 'approve'
            ? wait.type === 'permission_request'
              ? { decision: 'allow' }
              : { decision: 'approve' }
            : { decision: 'deny', reason: str('reason') };
      server.patch(agentId, wait.id, patch);
    }
    world.agents[agentId] = {
      ...agent,
      state: 'processing',
      pendingRequestId: null,
      waitingOn: '',
    };
    const cleared: string[] = [];
    for (const item of Object.values(world.attention)) {
      if (item.requestId === requestId && item.clearedAt === null) {
        world.attention[item.id] = { ...item, clearedAt: now() };
        cleared.push(item.id);
      }
    }
    for (const doc of Object.values(world.documents)) {
      if (doc.source.type === 'plan_review' && doc.source.requestId === requestId) {
        world.documents[doc.id] = {
          ...doc,
          status: action === 'approve' ? 'approved' : 'changes-requested',
        };
        server.change({ kind: 'document', ids: [doc.id] });
      }
    }
    server.change({ kind: 'agent', ids: [agentId] });
    if (cleared.length) server.change({ kind: 'attention', ids: cleared });
    return ok({});
  }

  m = pathname.match(/^\/api\/tasks\/(.+)\/launch$/);
  if (m && method === 'POST') {
    const task = world.tasks[m[1]!];
    if (!task) return notFound(`task ${m[1]}`);
    if (!task.actionable) return error(400, 'invalid', `Task ${task.id} is not actionable`);
    const agentId = `new${mint()}`;
    world.tasks[task.id] = { ...task, status: 'in-progress' };
    world.agents[agentId] = {
      id: agentId,
      parent: '',
      description: '',
      state: 'idle',
      session: `sess-${agentId}`,
      cwd: '',
      model: str('model') ?? '',
      permissionMode: '',
      waitingOn: '',
      lastMessage: '',
      costUsd: 0,
      taskId: task.id,
      project: task.project,
      worktreeId: '',
      exitCode: null,
      pendingRequestId: null,
    };
    world.desk[`task:${task.id}`] = { id: `task:${task.id}`, addedAt: now() };
    server.change({ kind: 'task', ids: [task.id] });
    server.change({ kind: 'agent', ids: [agentId] });
    server.change({ kind: 'desk', ids: [`task:${task.id}`] });
    return ok({ agentId });
  }

  m = pathname.match(/^\/api\/tasks\/(.+)\/status$/);
  if (m && method === 'POST') {
    const task = world.tasks[m[1]!];
    if (!task) return notFound(`task ${m[1]}`);
    world.tasks[task.id] = { ...task, status: str('status') as TaskStatus };
    server.change({ kind: 'task', ids: [task.id] });
    return ok({});
  }

  m = pathname.match(/^\/api\/tasks\/(.+)$/);
  if (m && method === 'PATCH') {
    const task = world.tasks[m[1]!];
    if (!task) return notFound(`task ${m[1]}`);
    world.tasks[task.id] = { ...task, ...(b as TaskEdit) };
    server.change({ kind: 'task', ids: [task.id] });
    return ok({});
  }

  if (pathname === '/api/tasks/infer' && method === 'POST') {
    const project = str('project') ?? '';
    const draft = str('draft')?.trim() ?? '';
    if (!world.projects[project]) return notFound(`project ${project}`);
    if (!draft) return error(400, 'invalid', 'Nothing to create');
    // The real server asks a model; this reads the draft's first line, so a
    // test gets a plausible naming without a subprocess.
    const title = draft.split('\n')[0]!.trim().slice(0, 80);
    const slug =
      title
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '')
        .split('-')
        .slice(0, 4)
        .join('-') || 'task';
    return ok({ title, branch: `feat/${slug}`, command: '', mode: 'auto' });
  }

  if (pathname === '/api/tasks' && method === 'POST') {
    const project = str('project') ?? '';
    if (!world.projects[project]) return notFound(`project ${project}`);
    const title = str('title')?.trim() ?? '';
    if (!title) return error(400, 'invalid', 'A title is required');
    const taskId = `${project}/NEW-${mint()}`;
    world.tasks[taskId] = {
      ...makeNewTask(taskId, project, title, b),
    };
    world.desk[`task:${taskId}`] = { id: `task:${taskId}`, addedAt: now() };
    server.change({ kind: 'task', ids: [taskId] });
    server.change({ kind: 'desk', ids: [`task:${taskId}`] });
    if (!b.launch) return ok({ taskId });
    const agentId = `new${mint()}`;
    world.tasks[taskId] = { ...world.tasks[taskId]!, status: 'in-progress' };
    world.agents[agentId] = makeNewAgent(agentId, {
      taskId,
      project,
      model: str('model') ?? '',
      mode: (str('mode') ?? '') as PermissionMode | '',
    });
    server.change({ kind: 'task', ids: [taskId] });
    server.change({ kind: 'agent', ids: [agentId] });
    return ok({ taskId, agentId });
  }

  if (pathname === '/api/agents' && method === 'POST') {
    const project = str('project') ?? '';
    if (!world.projects[project]) return notFound(`project ${project}`);
    if (!str('branch')?.trim()) return error(400, 'invalid', 'A branch is required');
    if (!str('prompt')?.trim()) return error(400, 'invalid', 'A prompt is required');
    const agentId = `new${mint()}`;
    // A free agent carries no task: that absence is what makes it free.
    world.agents[agentId] = makeNewAgent(agentId, {
      taskId: '',
      project,
      model: str('model') ?? '',
      mode: (str('mode') ?? '') as PermissionMode | '',
    });
    world.desk[`agent:${agentId}`] = { id: `agent:${agentId}`, addedAt: now() };
    server.change({ kind: 'agent', ids: [agentId] });
    server.change({ kind: 'desk', ids: [`agent:${agentId}`] });
    return ok({ agentId });
  }

  if (pathname === '/api/desk' && method === 'POST') {
    const id = str('id') ?? '';
    world.desk[id] = { id, addedAt: now() };
    server.change({ kind: 'desk', ids: [id] });
    return ok({});
  }
  m = pathname.match(/^\/api\/desk\/(.+)$/);
  if (m && method === 'DELETE') {
    const id = decodeURIComponent(m[1]!);
    if (!(id in world.desk)) return error(404, 'unknown_id', `${id} is not on the desk`);
    delete world.desk[id];
    server.change({ kind: 'desk', ids: [id] });
    return ok({});
  }
  return error(404, 'unknown_id', `No route ${key}`);
}

/** A task as the fake notebook writes a new one: the fields sent, the rest defaulted. */
function makeNewTask(
  id: TaskId,
  project: string,
  title: string,
  body: Record<string, unknown>,
): Task {
  const str = (name: string) => (body[name] === undefined ? '' : String(body[name]));
  return {
    id,
    notebookId: id.split('/')[1]!,
    project,
    title,
    content: str('content'),
    status: 'todo',
    branch: str('branch'),
    command: str('command'),
    mode: (str('mode') || 'plan') as TaskMode,
    priority: str('priority') || 'medium',
    model: str('model'),
    parent: '',
    follows: [],
    base: '',
    actionable: true,
    steps: [],
    log: [],
    created: new Date().toISOString(),
    updated: new Date().toISOString(),
  };
}

/** An agent as the fake host starts one, for a task or for nobody. */
function makeNewAgent(
  agentId: AgentId,
  over: { taskId: TaskId; project: string; model: string; mode: PermissionMode | '' },
): Agent {
  return {
    id: agentId,
    // A started agent is always top-level: only a child of one has these.
    parent: '',
    description: '',
    state: 'idle',
    session: `sess-${agentId}`,
    cwd: '',
    model: over.model,
    permissionMode: over.mode,
    waitingOn: '',
    lastMessage: '',
    costUsd: 0,
    taskId: over.taskId,
    project: over.project,
    worktreeId: '',
    exitCode: null,
    pendingRequestId: null,
  };
}
