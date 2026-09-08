import type { Attention } from '../protocol/attention';
import type { Agent, Phase, Task, Worktree } from '../protocol/entities';
import type { NodeState } from '../protocol/progress';
import { phaseForCommand } from '../protocol/phase';
import { progressOf } from '../protocol/progress';
import type { GraphNode } from '../selectors/graph';

/**
 * Nodes to look at without a server.
 *
 * The node's hardest problems are visual — whether a stopped run reads as
 * quieter than an idle one without reading as finished, whether the drained
 * phase bar still names its phase — and jsdom computes no layout, so tests
 * cannot answer them. These nodes let a story draw the real component in every
 * state, side by side, in both colour schemes.
 *
 * Every node is built through `progressOf`, never a hand-written `Progress`
 * — see DESIGN.md, "Seeing a change".
 */

function task(over: Partial<Task> = {}): Task {
  return {
    id: 'MAEL-40',
    notebookId: 'MAEL-40',
    project: 'maelstrom',
    title: 'Tell a stopped agent from an idle one',
    status: 'in-progress',
    command: '',
    mode: 'auto',
    branch: 'feat/idle-vs-stopped',
    parent: '',
    follows: [],
    priority: 'medium',
    model: '',
    base: '',
    content: '',
    steps: [],
    log: [],
    created: '2026-09-07T09:00:00Z',
    updated: '2026-09-08T11:00:00Z',
    actionable: true,
    ...over,
  };
}

function agent(over: Partial<Agent> = {}): Agent {
  return {
    id: 'f0a6f965',
    parent: '',
    description: '',
    state: 'processing',
    session: 'sess-1',
    cwd: '/Users/dev/Projects/maelstrom/maelstrom-kilo',
    model: 'claude-opus-5',
    permissionMode: 'auto',
    waitingOn: '',
    lastMessage: '',
    lastMessageAt: '',
    costUsd: 4.2,
    taskId: 'MAEL-40',
    project: 'maelstrom',
    worktreeId: 'maelstrom-kilo',
    exitCode: null,
    pendingRequestIds: [],
    pid: 4711,
    ...over,
  };
}

function worktree(over: Partial<Worktree> = {}): Worktree {
  return {
    id: 'maelstrom-kilo',
    project: 'maelstrom',
    nato: 'kilo',
    path: '/Users/dev/Projects/maelstrom/maelstrom-kilo',
    branch: 'feat/idle-vs-stopped',
    base: 'main',
    isClosed: false,
    dirtyFiles: 0,
    localCommits: 0,
    prNumber: null,
    prUrl: '',
    prState: '',
    prDraft: false,
    appUrl: '',
    appRunning: false,
    sessionCount: 1,
    ...over,
  };
}

/** One node, its progress read by the same function the board reads it with. */
function node(over: {
  id?: string;
  task?: Task | undefined;
  agent?: Agent | undefined;
  worktree?: Worktree;
}): GraphNode {
  const t = 'task' in over ? over.task : task();
  const a = 'agent' in over ? over.agent : agent();
  return {
    id: over.id ?? t?.id ?? a?.id ?? 'node',
    kind: t ? 'task' : 'freeAgent',
    task: t,
    agent: a,
    worktree: over.worktree ?? worktree(),
    progress: progressOf(t, a, []),
    // Read from the command, never set by hand: a free agent has no task and
    // so no phase, exactly as `deriveGraph` has it.
    phase: t ? phaseForCommand(t.command) : null,
    groupId: 'maelstrom',
    attention: [],
    reason: '',
    showProject: false,
  };
}

/**
 * A node per state, in the order the operator meets them: waiting, then
 * running, then ended. The pair this set exists to separate — `idle` and
 * `stopped` — sits adjacent on purpose.
 */
export const byState: { state: NodeState; label: string; node: GraphNode }[] = [
  {
    state: 'queued',
    label: 'Waiting on other work',
    node: node({ task: task({ status: 'todo', actionable: false }), agent: undefined }),
  },
  {
    state: 'ready',
    label: 'Waiting on the operator',
    node: node({ task: task({ status: 'todo' }), agent: undefined }),
  },
  { state: 'working', label: 'A turn in flight', node: node({}) },
  {
    state: 'needs-attention',
    label: 'Blocked on the operator',
    node: needsAttention(),
  },
  {
    state: 'idle',
    label: 'Process up, waiting at a prompt',
    node: node({ agent: agent({ state: 'idle' }) }),
  },
  {
    state: 'stopped',
    label: 'Process ended, a resume brings it back',
    node: node({ agent: agent({ state: 'exited', exitCode: 0 }) }),
  },
  {
    state: 'finalising',
    label: 'Task closed, an agent still carries the PR',
    node: node({ task: task({ status: 'done' }), agent: agent({ state: 'processing' }) }),
  },
  {
    state: 'done',
    label: 'Settled',
    node: node({ task: task({ status: 'done' }), agent: undefined }),
  },
  {
    state: 'cancelled',
    label: 'Terminal, not a success',
    node: node({ task: task({ status: 'cancelled' }), agent: undefined }),
  },
  {
    state: 'exited',
    label: 'A fault: exited non-zero',
    node: node({ agent: agent({ state: 'exited', exitCode: 1 }) }),
  },
];

/**
 * The one state that needs an open attention item to reach it. The item rides
 * on `attention` and `reason` as well as `progress`: `deriveGraph` fills all
 * three from the same item, so a node carrying only the progress would draw a
 * shape the board cannot produce -- the state words in place of the reason line.
 */
function needsAttention(): GraphNode {
  const t = task();
  const a = agent({ state: 'awaiting-permission', pendingRequestIds: ['req-1'] });
  const attention: Attention[] = [
    {
      id: 'att-1',
      kind: 'permission',
      taskId: t.id,
      agentId: a.id,
      documentId: null,
      requestId: 'req-1',
      summary: 'Run the full suite unsandboxed',
      raisedAt: '2026-09-08T11:02:00Z',
      // `isOpen` reads null, not empty: an item is open until it is cleared.
      clearedAt: null,
    },
  ];
  return {
    ...node({ task: t, agent: a }),
    progress: progressOf(t, a, attention),
    attention,
    reason: attention[0]!.summary,
  };
}

/** The pair the split exists for, drawn from one worktree so only state differs. */
export const idle = byState.find((n) => n.state === 'idle')!;
export const stopped = byState.find((n) => n.state === 'stopped')!;

/**
 * One stopped node per phase. The phase bar drains to `--phase-dormant`, and
 * this is the set that shows whether it still names its phase after draining.
 */
export const stoppedByPhase: { phase: Phase; node: GraphNode }[] = (
  [
    ['shape', 'shape'],
    ['plan', 'plan-task'],
    ['build', ''],
    ['land', 'watch-pr'],
  ] as const
).map(([phase, command]) => ({
  phase,
  node: node({
    id: `stopped-${phase}`,
    task: task({ command }),
    agent: agent({ state: 'exited', exitCode: 0 }),
  }),
}));
