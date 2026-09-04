import type { Attention } from '../protocol/attention';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import type { Document } from '../protocol/documents';
import type { Agent, Project, Task, Worktree } from '../protocol/entities';
import type { AgentId } from '../protocol/ids';
import { isActionable } from '../protocol/phase';
import type { Transcript, TranscriptItem } from '../protocol/transcript';
import type { FakeWorld } from './fakeServer';

/** The seed is stamped before this moment. */
export const SEED_TIME = '2026-09-02T09:00:00.000Z';

export interface Seed {
  world: FakeWorld;
  transcripts: Record<AgentId, Transcript>;
}

const T = (minutesAgo: number) =>
  new Date(Date.parse(SEED_TIME) - minutesAgo * 60_000).toISOString();

function project(id: string, stackTip: string): Project {
  return { id, name: id, stackTip };
}

function worktree(
  project: string,
  nato: string,
  over: Partial<Omit<Worktree, 'id' | 'project' | 'nato'>> = {},
): Worktree {
  return {
    id: `${project}-${nato}`,
    project,
    nato,
    path: `/Users/dev/Projects/${project}/${project}-${nato}`,
    branch: '',
    base: 'main',
    isClosed: false,
    dirtyFiles: 0,
    localCommits: 0,
    prNumber: null,
    appUrl: '',
    appRunning: false,
    sessionCount: 0,
    ...over,
  };
}

interface TaskSpec {
  id: string;
  project: string;
  title: string;
  status: Task['status'];
  branch: string;
  command?: string;
  parent?: string;
  follows?: string[];
  createdMinutesAgo?: number;
  content?: string;
}

function task(spec: TaskSpec): Task {
  const command = spec.command ?? '';
  const created = T(spec.createdMinutesAgo ?? 120);
  return {
    id: spec.id,
    // One project space, so a notebook id is already unique here; the real
    // server qualifies its own with the project.
    notebookId: spec.id,
    project: spec.project,
    title: spec.title,
    status: spec.status,
    command,
    mode: command === '' ? 'auto' : 'normal',
    branch: spec.branch,
    parent: spec.parent ?? '',
    follows: spec.follows ?? [],
    priority: 'medium',
    model: '',
    base: '',
    content: spec.content ?? `# ${spec.title}\n\n${spec.title} for ${spec.project}.\n`,
    steps: [],
    log: [],
    created,
    updated: created,
    actionable: false,
  };
}

function agent(id: string, t: Task, worktreeId: string, over: Partial<Agent> = {}): Agent {
  return {
    id,
    parent: '',
    description: '',
    state: 'processing',
    session: `sess-${id}`,
    cwd: `/Users/dev/Projects/${t.project}/${worktreeId}`,
    model: 'claude-opus-5',
    permissionMode: 'normal',
    waitingOn: '',
    lastMessage: '',
    costUsd: 0.42,
    taskId: t.id,
    project: t.project,
    worktreeId,
    exitCode: null,
    pendingRequestId: null,
    ...over,
  };
}

/** An agent with no task: started by hand, so no task session id links it. */
function freeAgent(
  id: string,
  project: string,
  worktreeId: string,
  over: Partial<Agent> = {},
): Agent {
  return {
    id,
    parent: '',
    description: '',
    state: 'processing',
    session: `sess-${id}`,
    cwd: `/Users/dev/Projects/${project}/${worktreeId}`,
    model: 'claude-opus-5',
    permissionMode: 'normal',
    waitingOn: '',
    lastMessage: '',
    costUsd: 0,
    taskId: '',
    project,
    worktreeId,
    exitCode: null,
    pendingRequestId: null,
    ...over,
  };
}

export const NORT7_PLAN = `# Order export

## Context

Customers want a CSV of their orders. The orders table already carries every
column the export needs, so this is a read path plus a download endpoint.

## Change

1. Add \`OrderExport\` in \`app/exports/orders.py\` that streams rows as CSV.
2. Add \`GET /orders/export\` behind the existing session auth.
3. Cap the export at 10,000 rows and return 413 above it.

## Seams under test

- \`OrderExport.rows()\` against an in-memory order set.
- The endpoint, with a client fixture: status, content type, first row.
`;

/**
 * The world the app tests run against: two projects, a plan awaiting review,
 * a question being asked, working agents, a free agent, history and a blocked
 * task. Every id the app tests name lives here.
 */
export function seedWorld(): Seed {
  seq = 0;
  const projects = [project('maelstrom', 'main'), project('northwind', 'feat/db-migrate')];

  const worktrees = [
    worktree('maelstrom', 'alpha', {
      branch: 'feat/orchestrator-ui',
      dirtyFiles: 3,
      sessionCount: 1,
    }),
    worktree('maelstrom', 'bravo', { branch: 'feat/task-index', localCommits: 2, sessionCount: 1 }),
    worktree('maelstrom', 'charlie', { isClosed: true }),
    worktree('northwind', 'alpha', { branch: 'feat/orders', sessionCount: 1 }),
    worktree('northwind', 'bravo', { branch: 'feat/db-migrate', dirtyFiles: 7, sessionCount: 1 }),
    worktree('northwind', 'charlie', { isClosed: true }),
    worktree('northwind', 'delta', {
      branch: 'feat/auth-rotation',
      prNumber: 118,
      appUrl: 'http://localhost:4210',
      appRunning: true,
      sessionCount: 1,
    }),
    worktree('northwind', 'echo', { isClosed: true }),
  ];

  const tasks: Task[] = [
    task({
      id: 'MAEL-40',
      project: 'maelstrom',
      title: 'Task index cache',
      status: 'done',
      branch: 'feat/task-index',
      parent: 'linear.MAEL-40',
      createdMinutesAgo: 400,
    }),
    task({
      id: 'MAEL-40.1',
      project: 'maelstrom',
      title: 'Restamp the index on HEAD change',
      status: 'in-progress',
      branch: 'feat/task-index',
      parent: 'linear.MAEL-40',
      follows: ['MAEL-40'],
      createdMinutesAgo: 380,
    }),
    task({
      id: 'MAEL-40.2',
      project: 'maelstrom',
      title: 'Watch the task index PR',
      status: 'todo',
      command: 'watch-pr',
      branch: 'feat/task-index',
      parent: 'linear.MAEL-40',
      follows: ['MAEL-40.1'],
      createdMinutesAgo: 380,
    }),
    task({
      id: 'MAEL-52',
      project: 'maelstrom',
      title: 'Shape the orchestrator UI',
      status: 'in-progress',
      command: 'shape',
      branch: 'feat/orchestrator-ui',
      parent: 'linear.MAEL-52',
      createdMinutesAgo: 90,
    }),
    task({
      id: 'MAEL-52.1',
      project: 'maelstrom',
      title: 'Plan the canvas',
      status: 'todo',
      command: 'plan-task',
      branch: 'feat/orchestrator-ui',
      parent: 'linear.MAEL-52',
      follows: ['MAEL-52'],
      createdMinutesAgo: 85,
    }),
    task({
      id: 'MAEL-52.2',
      project: 'maelstrom',
      title: 'Build the canvas',
      status: 'todo',
      branch: 'feat/orchestrator-ui',
      parent: 'linear.MAEL-52',
      follows: ['MAEL-52.1'],
      createdMinutesAgo: 85,
    }),
    task({
      id: 'NORT-7',
      project: 'northwind',
      title: 'Plan the order export',
      status: 'in-progress',
      command: 'plan-task',
      branch: 'feat/orders',
      parent: 'linear.NORT-7',
      createdMinutesAgo: 60,
    }),
    task({
      id: 'NORT-7.1',
      project: 'northwind',
      title: 'Build the order export',
      status: 'todo',
      branch: 'feat/orders',
      parent: 'linear.NORT-7',
      follows: ['NORT-7'],
      createdMinutesAgo: 55,
      content: `Export an order as CSV from the orders table.

## Seams under test

The HTTP endpoint. One fixture per column type, asserted through the response
body rather than the query builder.

## Steps

- Add the route and its serialiser.
- Stream the rows so a large export holds memory flat.
`,
    }),
    task({
      id: 'NORT-9',
      project: 'northwind',
      title: 'Migrate to Postgres 16',
      status: 'in-progress',
      branch: 'feat/db-migrate',
      parent: 'linear.NORT-9',
      createdMinutesAgo: 200,
    }),
    task({
      id: 'NORT-9.1',
      project: 'northwind',
      title: 'Watch the migration PR',
      status: 'todo',
      command: 'watch-pr',
      branch: 'feat/db-migrate',
      parent: 'linear.NORT-9',
      follows: ['NORT-9'],
      createdMinutesAgo: 200,
    }),
    task({
      id: 'NORT-12',
      project: 'northwind',
      title: 'Rotate auth tokens',
      status: 'in-progress',
      command: 'watch-pr',
      branch: 'feat/auth-rotation',
      parent: 'linear.NORT-12',
      createdMinutesAgo: 300,
    }),
    task({
      id: 'NORT-3',
      project: 'northwind',
      title: 'Fix the flaky checkout test',
      status: 'done',
      branch: 'fix/checkout-flake',
      createdMinutesAgo: 900,
    }),
    task({
      id: 'NORT-5',
      project: 'northwind',
      title: 'Spike GraphQL gateway',
      status: 'cancelled',
      branch: 'spike/graphql',
      createdMinutesAgo: 800,
    }),
    task({
      id: 'NORT-15',
      project: 'northwind',
      title: 'Shape the reporting module',
      status: 'blocked',
      command: 'shape',
      branch: 'feat/reporting',
      follows: ['NORT-12'],
      createdMinutesAgo: 40,
    }),
  ];

  const byId = Object.fromEntries(tasks.map((t) => [t.id, t]));
  for (const t of tasks) t.actionable = isActionable(t, byId);

  const nort7 = byId['NORT-7']!;
  const mael52 = byId['MAEL-52']!;
  const mael401 = byId['MAEL-40.1']!;
  const nort9 = byId['NORT-9']!;
  const nort12 = byId['NORT-12']!;

  const agents: Agent[] = [
    agent('a1f3c9e2', nort7, 'northwind-alpha', {
      state: 'awaiting-plan-review',
      permissionMode: 'plan',
      waitingOn: 'Plan: order export',
      lastMessage: 'The plan is ready for review.',
      pendingRequestId: 'req-nort7-plan',
      costUsd: 0.81,
    }),
    agent('b7d2e4a0', mael52, 'maelstrom-alpha', {
      state: 'awaiting-question',
      waitingOn: 'Should the canvas group by project or by branch first?',
      lastMessage: 'Two grouping defaults are plausible; I need a steer.',
      pendingRequestId: 'req-mael52-q',
      costUsd: 1.12,
    }),
    agent('c3e8f1b5', mael401, 'maelstrom-bravo', {
      lastMessage: 'Adding the HEAD staleness check to the index reader.',
      costUsd: 0.37,
    }),
    agent('d9a4c7f1', nort9, 'northwind-bravo', {
      lastMessage: 'Rewriting the migration for the new collation.',
      costUsd: 2.05,
    }),
    agent('e5b1d8c3', nort12, 'northwind-delta', {
      lastMessage: 'CI is red on the integration job; reading the log.',
      costUsd: 0.66,
    }),
    // A subagent of NORT-9's agent.
    agent('d9a4c7f1.1', nort9, 'northwind-bravo', {
      parent: 'd9a4c7f1',
      description: 'Find every collation-sensitive query',
      lastMessage: 'Three queries order by name without a collation.',
      costUsd: 0,
    }),
    freeAgent('f2c6a9d4', 'maelstrom', 'maelstrom-bravo', {
      lastMessage: 'Reading the index reader before I touch it.',
      costUsd: 0.19,
    }),
  ];

  const documents: Document[] = [
    {
      id: 'doc-nort7-plan',
      agentId: 'a1f3c9e2',
      taskId: 'NORT-7',
      kind: 'plan',
      title: 'plan.md',
      markdown: NORT7_PLAN,
      version: 1,
      status: 'awaiting-review',
      source: {
        type: 'plan_review',
        requestId: 'req-nort7-plan',
        planFilePath: '/Users/dev/.claude/plans/order-export.md',
      },
    },
  ];

  const attention: Attention[] = [
    {
      id: 'att-nort7-plan',
      kind: 'plan_review',
      agentId: 'a1f3c9e2',
      taskId: 'NORT-7',
      documentId: 'doc-nort7-plan',
      requestId: 'req-nort7-plan',
      summary: 'Plan awaiting review',
      raisedAt: T(4),
      clearedAt: null,
    },
    {
      id: 'att-mael52-q',
      kind: 'question',
      agentId: 'b7d2e4a0',
      taskId: 'MAEL-52',
      documentId: null,
      requestId: 'req-mael52-q',
      summary: 'Should the canvas group by project or by branch first?',
      raisedAt: T(9),
      clearedAt: null,
    },
  ];

  const world: FakeWorld = {
    projects: keyed(projects),
    worktrees: keyed(worktrees),
    tasks: byId,
    agents: keyed(agents),
    documents: keyed(documents),
    attention: keyed(attention),
    // The desk holds every task still in play, so the canvas opens with work
    // on it. Done and cancelled tasks live in the task list only. The free
    // agent is on the desk as the server's auto-join would put it there.
    desk: keyed([
      ...Object.values(byId)
        .filter((t) => t.status !== 'done' && t.status !== 'cancelled')
        .map((t) => ({ id: deskIdForTask(t.id), addedAt: T(120) })),
      ...agents.filter((a) => !a.taskId).map((a) => ({ id: deskIdForAgent(a.id), addedAt: T(30) })),
    ]),
  };

  const transcripts: Record<AgentId, Transcript> = {
    a1f3c9e2: transcript('a1f3c9e2', [
      init('a1f3c9e2', 30),
      message(
        'a1f3c9e2',
        'user',
        'Plan the order export. Read the brief in the task body first.',
        29,
      ),
      message('a1f3c9e2', 'assistant', 'Reading the orders model and the existing exports.', 28),
      tool(
        'a1f3c9e2',
        'Read',
        { file_path: 'app/models/order.py' },
        'class Order(Base):\n    ...',
        27,
      ),
      message('a1f3c9e2', 'assistant', 'The plan is ready for review.', 5),
      {
        id: 'a1f3c9e2-plan',
        ts: T(4),
        type: 'plan_review',
        requestId: 'req-nort7-plan',
        documentId: 'doc-nort7-plan',
      },
    ]),
    b7d2e4a0: transcript('b7d2e4a0', [
      init('b7d2e4a0', 40),
      message('b7d2e4a0', 'user', 'Shape the orchestrator UI from the brief.', 39),
      message('b7d2e4a0', 'assistant', 'Two grouping defaults are plausible; I need a steer.', 10),
      {
        id: 'b7d2e4a0-q',
        ts: T(9),
        type: 'question',
        requestId: 'req-mael52-q',
        questions: [
          {
            question: 'Should the canvas group by project or by branch first?',
            header: 'Grouping',
            multiSelect: false,
            options: [
              { label: 'Project', description: 'One lane per project; branches inside.' },
              { label: 'Branch', description: 'One lane per branch, labelled with its worktree.' },
            ],
          },
        ],
      },
    ]),
    c3e8f1b5: transcript('c3e8f1b5', [
      init('c3e8f1b5', 20),
      message('c3e8f1b5', 'user', 'Restamp the index on HEAD change.', 19),
      message('c3e8f1b5', 'assistant', 'Adding the HEAD staleness check to the index reader.', 3),
    ]),
    d9a4c7f1: transcript('d9a4c7f1', [
      init('d9a4c7f1', 120),
      message('d9a4c7f1', 'user', 'Migrate to Postgres 16.', 119),
      tool(
        'd9a4c7f1',
        'Agent',
        { description: 'Find every collation-sensitive query', prompt: 'Grep for ORDER BY name.' },
        'Three queries order by name without a collation.',
        3,
      ),
      message('d9a4c7f1', 'assistant', 'Rewriting the migration for the new collation.', 2),
    ]),
    'd9a4c7f1.1': transcript('d9a4c7f1.1', [
      message('d9a4c7f1.1', 'user', 'Grep for ORDER BY name.', 4),
      tool('d9a4c7f1.1', 'Bash', { command: 'grep -rn "ORDER BY name" app/' }, 'app/q.py:12', 4),
      message('d9a4c7f1.1', 'assistant', 'Three queries order by name without a collation.', 3),
    ]),
    e5b1d8c3: transcript('e5b1d8c3', [
      init('e5b1d8c3', 15),
      message('e5b1d8c3', 'user', 'Watch PR #118 and take CI to green.', 14),
      message('e5b1d8c3', 'assistant', 'CI is red on the integration job; reading the log.', 1),
    ]),
  };

  return { world, transcripts };
}

function keyed<T extends { id: string }>(items: T[]): Record<string, T> {
  return Object.fromEntries(items.map((i) => [i.id, i]));
}

function transcript(agentId: string, items: TranscriptItem[]): Transcript {
  return { agentId, items, truncatedBefore: false };
}

function init(agentId: string, minutesAgo: number): TranscriptItem {
  return {
    id: `${agentId}-init`,
    ts: T(minutesAgo),
    type: 'system',
    subtype: 'init',
    sessionId: `sess-${agentId}`,
    model: 'claude-opus-5',
  };
}

// Reset at the top of seedWorld(), so two seeds in one process agree on ids.
let seq = 0;
function message(
  agentId: string,
  role: 'user' | 'assistant',
  markdown: string,
  minutesAgo: number,
): TranscriptItem {
  seq += 1;
  return { id: `${agentId}-m${seq}`, ts: T(minutesAgo), type: 'message', role, markdown };
}

function tool(
  agentId: string,
  toolName: string,
  input: Record<string, unknown>,
  output: string,
  minutesAgo: number,
): TranscriptItem {
  seq += 1;
  return {
    id: `${agentId}-t${seq}`,
    ts: T(minutesAgo),
    type: 'tool_call',
    toolUseId: `toolu_seed_${seq}`,
    tool: toolName,
    input,
    status: 'done',
    output,
  };
}
