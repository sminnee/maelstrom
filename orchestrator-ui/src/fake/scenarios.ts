import type { Attention, AttentionKind } from '../protocol/attention';
import { deskIdForTask } from '../protocol/deskId';
import type { DocumentKind, DocumentStatus } from '../protocol/documents';
import type { Agent, FileDiff, PrState, Task, Worktree } from '../protocol/entities';
import type { ToolCallStatus, TranscriptItem } from '../protocol/transcript';
import {
  makeAgent,
  makeAttention,
  makeDocument,
  makeProject,
  makeTask,
  makeWorktree,
  onDesk,
  worldWith,
} from './fixtures';
import { comm, seedWorld, T, task, type Seed } from './seedWorld';
import { defaultLoc, toHref, withLoc, type LocPatch } from '../nav/location';
import { changesTab, devEnvTab, sessionTab } from '../selectors/tabs';
import type { UiState } from '../store/uiSlice';

/**
 * The worlds the fake mode opens on. Each one is a state of the product that a
 * person must be able to look at: `/scenario/<name>/desk` draws it. See
 * `scenarios.test.tsx`.
 */
export interface Scenario {
  /** What the scenario shows, for the index page. */
  about: string;
  build: () => Seed;
  /** The URL of the screen the scenario exists to show: a path and a search. */
  screen?: string;
  /** UI state the URL cannot carry, such as a split tab. */
  ui?: Partial<UiState>;
}

/** The URL of the desk with `patch` laid over it. */
const at = (patch: LocPatch) => toHref(withLoc(defaultLoc(), patch));

const NATO = [
  'alpha',
  'bravo',
  'charlie',
  'delta',
  'echo',
  'foxtrot',
  'golf',
  'hotel',
  'india',
  'juliett',
  'kilo',
  'lima',
  'mike',
  'november',
];

/** One node of `every-state`: a task on its own worktree, with or without an agent. */
interface NodeSpec {
  title: string;
  task: Partial<Task>;
  agent?: Partial<Agent>;
  worktree?: Partial<Worktree>;
  attention?: AttentionKind;
}

/**
 * One row per node state, in the order `NodeState` lists them, then the rows
 * that exist for an agent state, a drift or a task status alone. The PR states
 * are spread over the rows, so each one draws on a real node.
 */
const NODES: NodeSpec[] = [
  { title: 'Queued behind other work', task: { status: 'todo', actionable: false } },
  { title: 'Ready to launch', task: { status: 'todo', command: 'shape' } },
  {
    title: 'Working',
    task: { status: 'in-progress', command: 'plan-next-step' },
    agent: { state: 'processing' },
    worktree: { prState: 'ci-running', env: partialEnv() },
  },
  {
    title: 'Needs a permission',
    task: { status: 'in-progress' },
    agent: { state: 'awaiting-permission', waitingOn: 'Bash: rm -rf build' },
    attention: 'permission',
    worktree: { prState: 'conflict' },
  },
  {
    title: 'Idle with nothing said',
    task: { status: 'in-progress' },
    agent: { state: 'idle' },
    worktree: { prState: 'unknown' },
  },
  {
    title: 'Unanswered',
    task: { status: 'in-progress' },
    agent: { state: 'idle', lastMessage: 'Which of the two do you want?', lastMessageAt: T(7) },
    worktree: { prState: 'checks-unreadable' },
  },
  {
    title: 'Stopped, and finished',
    task: { status: 'in-progress' },
    agent: { state: 'exited', exitCode: 0 },
    worktree: { prState: 'ready', env: { state: 'running', services: [] } },
  },
  {
    title: 'Finalising after the PR push',
    task: { status: 'done', command: 'watch-pr' },
    agent: { state: 'processing' },
    worktree: { prState: 'ci-running' },
  },
  { title: 'Done', task: { status: 'done' }, worktree: { prState: 'merged', prMergedAt: T(600) } },
  { title: 'Cancelled', task: { status: 'cancelled' } },
  {
    title: 'Exited with a fault',
    task: { status: 'in-progress' },
    agent: { state: 'exited', exitCode: 1 },
    attention: 'agent_exited',
    worktree: { prState: 'ci-failed' },
  },
  {
    title: 'Subagents working',
    task: { status: 'in-progress' },
    agent: { state: 'delegating' },
  },
  {
    title: 'Background shell running',
    task: { status: 'in-progress' },
    agent: {
      state: 'background',
      backgroundShells: [{ id: 'sh-1', description: 'Watch the test run' }],
    },
  },
  { title: 'In progress, and never ran', task: { status: 'in-progress' } },
  {
    title: 'Not started, with a session',
    task: { status: 'todo' },
    agent: { state: 'idle' },
  },
  { title: 'Blocked', task: { status: 'blocked' }, attention: 'task_blocked' },
  { title: 'A template', task: { status: 'template' } },
];

function partialEnv(): Worktree['env'] {
  return {
    state: 'partial',
    services: [
      { name: 'web', optional: false, running: true, url: 'http://localhost:4210' },
      { name: 'orchestrator', optional: false, running: false, url: '' },
      { name: 'ladle', optional: true, running: false, url: 'http://localhost:4212' },
    ],
  };
}

function everyState(): Seed {
  const tasks: Task[] = [];
  const agents: Agent[] = [];
  const worktrees: Worktree[] = [makeWorktree({ id: '_main', nato: '_main', branch: 'main' })];
  const attention: Attention[] = [];
  NODES.forEach((spec, i) => {
    const id = `NORT-${i + 20}`;
    const branch = `feat/state-${i}`;
    const nato = NATO[i % NATO.length]!;
    // More nodes than names: the tail has no worktree, as a branch nobody opened.
    const worktreeId = i < NATO.length ? `northwind-${nato}` : '';
    const task = makeTask({ id, notebookId: id, title: spec.title, branch, ...spec.task });
    tasks.push(task);
    if (worktreeId) {
      const pr: PrState | '' = spec.worktree?.prState ?? '';
      worktrees.push(
        makeWorktree({
          id: worktreeId,
          nato,
          branch,
          path: `/Users/dev/Projects/northwind/${worktreeId}`,
          prNumber: pr ? 100 + i : null,
          prCommits: pr ? 2 : null,
          prUrl: pr ? `https://github.com/acme/northwind/pull/${100 + i}` : '',
          sessionCount: spec.agent ? 1 : 0,
          ...spec.worktree,
        }),
      );
    }
    if (spec.agent) {
      agents.push(
        makeAgent({
          id: `agent-${i}`,
          session: `sess-${i}`,
          taskId: id,
          worktreeId,
          pendingRequestIds: spec.agent.state?.startsWith('awaiting-') ? [`req-${i}`] : [],
          ...spec.agent,
        }),
      );
    }
    if (spec.attention) {
      attention.push(
        makeAttention({
          id: `att-${i}`,
          kind: spec.attention,
          agentId: spec.agent ? `agent-${i}` : null,
          taskId: id,
          requestId: spec.attention === 'permission' ? `req-${i}` : null,
          summary: spec.title,
          raisedAt: T(5),
        }),
      );
    }
  });
  const world = worldWith({
    projects: [makeProject()],
    worktrees,
    tasks,
    agents,
    attention,
    desk: onDesk(tasks),
  });
  return { world, transcripts: {} };
}

/** A task id of the length the notebook mints. A short one flatters a layout. */
const DETAIL_TASK = 'maelstrom/2026-09-22.1';
const DETAIL_AGENT = 'f81c22ab';
const DETAIL_WORKTREE = 'maelstrom-lima';

const doc = (kind: DocumentKind, title: string, status: DocumentStatus, markdown: string) =>
  makeDocument({
    id: `doc-detail-${kind}`,
    agentId: DETAIL_AGENT,
    taskId: DETAIL_TASK,
    kind,
    title,
    status,
    markdown,
    source: { type: 'draft_file', fileId: `detail-${kind}`, filename: `.drafts/${kind}.md` },
  });

/**
 * A finished task with everything a card can carry: a milestone band, a PR, an
 * env, and one document of each kind. The narrow detail screen is drawn for it.
 */
function detail(): Seed {
  const seed = seedWorld();
  const { world } = seed;
  const task = makeTask({
    id: DETAIL_TASK,
    notebookId: '2026-09-22.1',
    project: 'maelstrom',
    title: 'Standardise UI spacing and add a fake mode for UI work',
    status: 'done',
    branch: 'refactor/ui-spacing-preview',
    content: '# Standard spacing\n\nOne unit, one control size.\n',
    created: T(300),
    updated: T(20),
    startedAt: T(290),
    prNumber: 412,
    prUrl: 'https://github.com/acme/maelstrom/pull/412',
  });
  world.tasks[task.id] = task;
  world.desk[deskIdForTask(task.id)] = { id: deskIdForTask(task.id), addedAt: T(300) };
  world.worktrees[DETAIL_WORKTREE] = makeWorktree({
    id: DETAIL_WORKTREE,
    project: 'maelstrom',
    nato: 'lima',
    path: '/Users/dev/Projects/maelstrom/maelstrom-lima',
    branch: task.branch,
    dirtyFiles: 2,
    localCommits: 5,
    pushedCommits: 5,
    prNumber: 412,
    prCommits: 5,
    prUrl: 'https://github.com/acme/maelstrom/pull/412',
    prState: 'ready',
    env: partialEnv(),
    sessionCount: 1,
    shellUrl: 'cmux://workspace/WS-LIMA/pane/PANE-LIMA',
  });
  world.agents[DETAIL_AGENT] = makeAgent({
    id: DETAIL_AGENT,
    session: `sess-${DETAIL_AGENT}`,
    state: 'idle',
    taskId: task.id,
    project: 'maelstrom',
    worktreeId: DETAIL_WORKTREE,
    cwd: '/Users/dev/Projects/maelstrom/maelstrom-lima',
    lastMessage: 'The PR is up and CI is green.',
    lastMessageAt: T(20),
    costUsd: 14.2,
    totalTokens: 2_410_000,
    contextTokens: 88_000,
  });
  world.milestones[DETAIL_AGENT] = [
    stage('planned', 280, 210_000, 210_000),
    stage('built', 90, 1_900_000, 1_690_000),
    stage('reviewed', 50, 2_200_000, 300_000),
    stage('presented', 25, 2_410_000, 210_000),
  ];
  const docs = [
    makeDocument({
      id: 'doc-detail-plan',
      agentId: DETAIL_AGENT,
      taskId: DETAIL_TASK,
      title: 'Plan',
      status: 'approved',
      markdown: '# Standard spacing\n\nThe unit is 8px.\n',
      source: { type: 'plan_review', requestId: 'req-detail-plan', planFilePath: '' },
    }),
    doc('tasks', 'Iteration 1', 'superseded', '## Execute\n\nOne step.\n'),
    doc('pr', 'Pull request', 'draft', '# Standard spacing\n\nFive tokens replace six.\n'),
    doc('review', 'Code review', 'changes-requested', '# Review\n\nTwo findings.\n'),
    doc('verification', 'Narrow layout', 'draft', '# Narrow layout\n\nChecked at 390px.\n'),
    doc('other', 'Spacing inventory', 'stale', '# Inventory\n\n370 values.\n'),
  ];
  for (const d of docs) world.documents[d.id] = d;
  return seed;
}

function stage(name: string, minutesAgo: number, total: number, delta: number) {
  return {
    name,
    at: T(minutesAgo),
    recognised: true,
    total_tokens: total,
    delta_tokens: delta,
    own_delta: delta,
    subagent_delta: 0,
    cost_delta: delta / 170_000,
  };
}

const diff = (path: string, over: Partial<FileDiff> = {}): FileDiff => ({
  path,
  oldPath: null,
  status: 'modified',
  binary: false,
  additions: 2,
  deletions: 1,
  truncated: false,
  hunks: [
    {
      header: '@@ -4,3 +4,4 @@ def rotate(token):',
      lines: [
        { kind: 'context', text: '    now = clock()', oldLine: 4, newLine: 4 },
        { kind: 'remove', text: '    if token.expiry < now:', oldLine: 5, newLine: null },
        { kind: 'add', text: '    if token.expiry <= now:', oldLine: null, newLine: 5 },
        {
          kind: 'add',
          text: '        log.info("rotating %s", token.id)',
          oldLine: null,
          newLine: 6,
        },
        { kind: 'context', text: '        return refresh(token)', oldLine: 6, newLine: 7 },
      ],
    },
  ],
  ...over,
});

const CHANGES_SHA = 'c0ffee1234567890c0ffee1234567890c0ffee12';
const CHANGES_LATER_SHA = 'decade1234567890decade1234567890decade12';

/** NORT-12's worktree with dirty files and two commits, covering every diff status. */
function changes(): Seed {
  const seed = seedWorld();
  const every = [
    diff('auth/tokens.py'),
    diff('auth/rotate.py', { status: 'added', deletions: 0 }),
    diff('auth/legacy.py', { status: 'deleted', additions: 0 }),
    diff('auth/expiry.py', { status: 'renamed', oldPath: 'auth/ttl.py' }),
    diff('docs/tokens.png', { binary: true, additions: 0, deletions: 0, hunks: [] }),
    diff('auth/fixtures/tokens.json', { truncated: true, additions: 4200 }),
  ];
  seed.world.worktrees['northwind-delta']!.dirtyFiles = 2;
  seed.world.changes['northwind-delta'] = {
    changes: {
      dirtyFiles: [
        { path: 'auth/tokens.py', status: 'M' },
        { path: 'auth/rotate.py', status: '?' },
      ],
      base: 'main',
      commits: [
        {
          sha: CHANGES_SHA,
          shortSha: 'c0ffee1',
          subject: 'feat: rotate on expiry',
          body: 'A token past its expiry now rotates.\n\nThe old one stays:\n- for a minute\n- once',
          author: 'Sam',
          date: T(240),
          filesChanged: every.length,
        },
        {
          sha: CHANGES_LATER_SHA,
          shortSha: 'decade1',
          subject: 'fix: keep the old token a minute',
          body: '',
          author: 'Sam',
          date: T(180),
          filesChanged: 1,
        },
      ],
    },
    diffs: {
      uncommitted: every.slice(0, 2),
      branch: every,
      [CHANGES_SHA]: every,
      [CHANGES_LATER_SHA]: every.slice(0, 1),
    },
  };
  return seed;
}

const ASK_AGENT = 'd9a4c7f1';
const ASK_TASK = 'NORT-9';

/**
 * Every way an agent or the server asks for the operator. NORT-9's agent holds
 * a permission ask, an open question and a plan review at once; the other
 * attention kinds are raised on tasks of the seed.
 */
function asks(): Seed {
  const seed = seedWorld();
  const { world } = seed;
  const items: TranscriptItem[] = [
    {
      id: 'ask-q-answered',
      ts: T(30),
      type: 'question',
      requestId: 'req-ask-answered',
      questions: [question('Stream or batch?', 'Export', false, ['Stream', 'Batch'])],
      answers: { 'Stream or batch?': 'Stream' },
    },
    {
      id: 'ask-q-declined',
      ts: T(25),
      type: 'question',
      requestId: 'req-ask-declined',
      questions: [question('Drop the old index?', 'Index', false, ['Drop', 'Keep'])],
      declined: true,
      reason: 'Not yet. Finish the migration first.',
    },
    {
      id: 'ask-perm-allowed',
      ts: T(20),
      type: 'permission_request',
      requestId: 'req-ask-allowed',
      tool: 'Bash',
      input: { command: 'pg_dump northwind > before.sql' },
      description: 'Dump the database',
      decision: 'allow',
    },
    {
      id: 'ask-perm-denied',
      ts: T(18),
      type: 'permission_request',
      requestId: 'req-ask-denied',
      tool: 'Bash',
      input: { command: 'dropdb northwind' },
      description: 'Drop the database',
      decision: 'deny',
      reason: 'Never on this host.',
    },
    {
      id: 'ask-plan',
      ts: T(6),
      type: 'plan_review',
      requestId: 'req-ask-plan',
      documentId: 'doc-ask-plan',
    },
    {
      id: 'ask-perm-open',
      ts: T(4),
      type: 'permission_request',
      requestId: 'req-ask-perm',
      tool: 'Bash',
      input: { command: 'psql -f migrations/016_collation.sql' },
      description: 'Run the collation migration',
    },
    {
      id: 'ask-q-open',
      ts: T(2),
      type: 'question',
      requestId: 'req-ask-q',
      questions: [
        question('Which columns?', 'Columns', true, ['Id', 'Total', 'Customer']),
        question('Stream or batch?', 'Export', false, ['Stream', 'Batch']),
      ],
    },
  ];
  seed.transcripts[ASK_AGENT]!.items.push(...items);
  world.agents[ASK_AGENT] = {
    ...world.agents[ASK_AGENT]!,
    state: 'awaiting-question',
    waitingOn: 'Which columns?',
    pendingRequestIds: ['req-ask-plan', 'req-ask-perm', 'req-ask-q'],
  };
  world.documents['doc-ask-plan'] = makeDocument({
    id: 'doc-ask-plan',
    agentId: ASK_AGENT,
    taskId: ASK_TASK,
    title: 'Plan',
    markdown: '# Migrate to Postgres 16\n\nOne collation at a time.\n',
    source: { type: 'plan_review', requestId: 'req-ask-plan', planFilePath: '' },
  });
  const raise = (kind: AttentionKind, over: Partial<Attention>) =>
    makeAttention({
      id: `att-ask-${kind}`,
      kind,
      agentId: ASK_AGENT,
      taskId: ASK_TASK,
      requestId: null,
      raisedAt: T(3),
      ...over,
    });
  const raised = [
    raise('plan_review', {
      requestId: 'req-ask-plan',
      documentId: 'doc-ask-plan',
      summary: 'Plan awaiting review',
    }),
    raise('permission', { requestId: 'req-ask-perm', summary: 'Run the collation migration' }),
    raise('question', { requestId: 'req-ask-q', summary: 'Which columns?' }),
    raise('agent_exited', {
      agentId: 'e5b1d8c3',
      taskId: 'NORT-12',
      summary: 'The agent exited with code 1',
    }),
    raise('ci_failed', { agentId: null, taskId: 'NORT-12', summary: 'CI failed on PR #118' }),
    raise('task_blocked', { agentId: null, taskId: 'NORT-15', summary: 'Blocked on NORT-12' }),
  ];
  for (const a of raised) world.attention[a.id] = a;
  world.agents['e5b1d8c3'] = { ...world.agents['e5b1d8c3']!, state: 'exited', exitCode: 1 };
  world.worktrees['northwind-delta']!.prState = 'ci-failed';
  return seed;
}

function question(text: string, header: string, multiSelect: boolean, labels: string[]) {
  return {
    question: text,
    header,
    multiSelect,
    options: labels.map((label) => ({ label, description: `Choose ${label.toLowerCase()}.` })),
  };
}

const TOOL_STATUSES: ToolCallStatus[] = ['pending', 'running', 'done', 'error', 'denied'];

/** MAEL-40.1's session, long, with every item type and every tool-call status. */
function transcript(): Seed {
  const seed = seedWorld();
  const agentId = 'c3e8f1b5';
  let n = 0;
  const base = () => {
    n += 1;
    return { id: `long-${n}`, ts: T(Math.max(0, 200 - n)) };
  };
  const items: TranscriptItem[] = [
    { ...base(), type: 'system', subtype: 'init', sessionId: `sess-${agentId}`, model: 'opus' },
    { ...base(), type: 'skill', skill: 'mael', markdown: '# Maelstrom workflow\n\nUse `mael`.\n' },
    { ...base(), type: 'message', role: 'user', markdown: 'Restamp the index on HEAD change.' },
  ];
  for (let round = 0; round < 8; round += 1) {
    items.push({
      ...base(),
      type: 'message',
      role: 'assistant',
      markdown: `Round ${round + 1}. The reader stamps the index with the HEAD it scanned, so a moved HEAD makes the stamp stale.\n\n- read the stamp\n- compare it with \`git rev-parse HEAD\`\n- rescan when they differ`,
    });
    for (const status of TOOL_STATUSES) {
      items.push({
        ...base(),
        type: 'tool_call',
        toolUseId: `toolu-long-${n}`,
        tool: status === 'done' ? 'Read' : 'Bash',
        input:
          status === 'done'
            ? { file_path: 'lib/domain/src/mael_domain/task_index.py' }
            : { command: 'uv run pytest lib/domain/tests/test_task_index.py', description: 'Run' },
        status,
        output: status === 'error' ? 'FAILED test_restamp - AssertionError' : 'ok',
      });
    }
  }
  items.push(
    {
      ...base(),
      type: 'shell',
      command: 'git status --short',
      output: ' M task_index.py',
      status: 'done',
    },
    {
      ...base(),
      type: 'permission_request',
      requestId: 'req-long-perm',
      tool: 'Write',
      input: { file_path: 'lib/domain/src/mael_domain/task_index.py' },
      description: 'Write task_index.py',
      decision: 'allow',
    },
    {
      ...base(),
      type: 'question',
      requestId: 'req-long-q',
      questions: [question('Restamp on a partial scan?', 'Restamp', false, ['Yes', 'No'])],
      answers: { 'Restamp on a partial scan?': 'No' },
    },
    {
      ...base(),
      type: 'plan_review',
      requestId: 'req-long-plan',
      documentId: null,
      decision: 'approve',
    },
    { ...base(), type: 'task_notification', status: 'completed', summary: 'The test run finished' },
    { ...base(), type: 'compact', trigger: 'auto', preTokens: 168_000, postTokens: 21_000 },
    { ...base(), type: 'compact_summary', markdown: 'The reader now restamps on a full scan.' },
    { ...base(), type: 'gap', droppedEvents: 12 },
    { ...base(), type: 'error', message: 'API Error: 529 overloaded' },
    { ...base(), type: 'raw_event', method: 'system/unknown', params: { note: 'not yet read' } },
    {
      ...base(),
      type: 'milestone',
      name: 'built',
      recognised: true,
      deltaTokens: 19_500,
      costDelta: 0.24,
    },
    { ...base(), type: 'turn_result', subtype: 'success', costUsd: 0.37, durationMs: 412_000 },
    {
      ...base(),
      type: 'message',
      role: 'assistant',
      markdown: 'Adding the HEAD staleness check to the index reader.',
    },
  );
  seed.transcripts[agentId] = { agentId, items, truncatedBefore: true };
  return seed;
}

function empty(): Seed {
  return { world: worldWith({ projects: [makeProject()] }), transcripts: {} };
}

/**
 * The seed with both usage windows read: the 5-hour ahead of pace, so it is
 * amber and the narrow bar shows it, and the week on pace and neutral.
 *
 * Dated from the page load rather than {@link T}: the app reads the real
 * clock, and a reading stamped at the seed's time would draw as stale.
 */
function usage(): Seed {
  const seed = seedWorld();
  const now = Date.now();
  const resetsIn = (hours: number) => Math.round(now / 1000 + hours * 3600);
  seed.world.host = {
    ...seed.world.host!,
    usage: {
      fiveHour: { utilization: 0.55, resetsAt: resetsIn(3) },
      sevenDay: { utilization: 0.05, resetsAt: resetsIn(6 * 24) },
      at: new Date(now).toISOString(),
    },
  };
  return seed;
}

/** The changes scenario, with a ready PR whose head is not the local branch. */
function prDiffers(): Seed {
  const seed = changes();
  const delta = seed.world.worktrees['northwind-delta']!;
  seed.world.worktrees['northwind-delta'] = { ...delta, prState: 'ready', prMatch: 'differ' };
  return seed;
}

/**
 * Three comms over the seed: two open and one closed, and a done task at each
 * landing step. `c1` has reached live, `c2` only merged — one of its tasks is
 * still in progress, and one has no Registered PR, so it has no env to read.
 */
function comms(): Seed {
  const seed = seedWorld();
  const { world } = seed;
  const done = [
    task({
      id: 'NORT-20',
      project: 'northwind',
      title: 'Export orders as CSV',
      status: 'done',
      branch: 'feat/order-export-csv',
      prNumber: 120,
      landing: { status: 'live', envs: { uat: 'landed', live: 'landed' } },
      comms: ['c1'],
    }),
    task({
      id: 'NORT-21',
      project: 'northwind',
      title: 'Cap the export at 10,000 rows',
      status: 'done',
      branch: 'feat/order-export-cap',
      prNumber: 121,
      landing: { status: 'uat', envs: { uat: 'landed', live: 'not_yet' } },
      comms: ['c1'],
    }),
    task({
      id: 'NORT-22',
      project: 'northwind',
      title: 'Retry a failed refund webhook',
      status: 'done',
      branch: 'feat/refund-retry',
      prNumber: 122,
      landing: { status: 'merged', envs: { uat: 'not_yet', live: 'unknown' } },
      comms: ['c2'],
    }),
    task({
      id: 'NORT-23',
      project: 'northwind',
      title: 'Reword the refund email',
      status: 'done',
      branch: 'feat/refund-email',
      landing: { status: 'done', envs: {} },
      comms: ['c2'],
    }),
  ];
  for (const t of done) world.tasks[t.id] = t;
  world.tasks['NORT-12'] = { ...world.tasks['NORT-12']!, comms: ['c2'] };
  world.tasks['NORT-3'] = {
    ...world.tasks['NORT-3']!,
    landing: { status: 'live', envs: { uat: 'landed', live: 'landed' } },
    comms: ['c3'],
  };
  for (const c of [
    comm({
      id: 'c1',
      title: 'Order export is live',
      content: 'Sales asked to hear when customers can download their orders.',
      recipients: ['#sales', 'ops@northwind.test'],
    }),
    comm({
      id: 'c2',
      title: 'Refunds retry on their own',
      content: 'Support wants to stop retrying failed refunds by hand.',
      recipients: ['#support'],
    }),
    comm({
      id: 'c3',
      title: 'Checkout test is stable',
      recipients: ['#eng'],
      closedMinutesAgo: 60,
    }),
  ]) {
    world.comms[c.id] = c;
  }
  return seed;
}

const LONG_BRANCH = 'feat/rotate-auth-tokens-for-every-service-and-keep-the-old-one-for-a-minute';

/**
 * The changes scenario with every title too long for a phone. No view may
 * scroll sideways on it. See DESIGN.md, "The Wide Content Rule".
 */
function longText(): Seed {
  const seed = changes();
  const { world } = seed;
  world.tasks['NORT-12'] = {
    ...world.tasks['NORT-12']!,
    notebookId: 'northwind/2026-09-22.1-rotate-auth-tokens',
    title:
      'Rotate auth tokens in northwind/services/auth/token_rotation/scheduler_configuration.py and keep the old one for a minute',
    branch: LONG_BRANCH,
    comms: ['c-long'],
  };
  world.worktrees['northwind-delta'] = {
    ...world.worktrees['northwind-delta']!,
    branch: LONG_BRANCH,
  };
  const commits = world.changes['northwind-delta']!.changes.commits;
  commits[0] = {
    ...commits[0]!,
    subject:
      'feat: rotate an auth token on expiry in services/auth/token_rotation/scheduler_configuration.py',
  };
  const doc = world.documents['doc-nort12-tasks']!;
  world.documents[doc.id] = {
    ...doc,
    title:
      'Iteration 2: rotate the tokens in services/auth/token_rotation/scheduler_configuration.py',
  };
  const deep = diff('northwind/services/auth/token_rotation/scheduler/configuration/defaults.py');
  const diffs = world.changes['northwind-delta']!.diffs;
  diffs[CHANGES_SHA] = [...diffs[CHANGES_SHA]!, deep];
  world.comms['c-long'] = comm({
    id: 'c-long',
    title:
      'Auth tokens now rotate on their own across every northwind service, and the old token stays valid for a minute',
    recipients: ['#security-and-platform-engineering', 'platform-announcements@northwind.test'],
  });
  return seed;
}

function hostDown(): Seed {
  const seed = seedWorld();
  seed.world.host = { ...seed.world.host!, reachable: false, since: T(3) };
  return seed;
}

export const SCENARIOS = {
  desk: { about: 'The seed: two projects, working agents and three open asks.', build: seedWorld },
  'every-state': {
    about: 'One node for each node state, each PR state, and a partial env.',
    build: everyState,
  },
  detail: {
    about: 'A finished task with a milestone band, a PR, and a document of each kind.',
    build: detail,
    screen: at({ card: { kind: 'task', id: DETAIL_TASK } }),
  },
  changes: {
    about: 'A worktree with dirty files and commits, and a diff of each status.',
    build: changes,
    screen: at({ panel: changesTab('northwind-delta') }),
  },
  'pr-differs': {
    about: 'A ready PR whose head is not the local branch: Merge waits for a sync.',
    build: prDiffers,
    screen: at({ card: { kind: 'task', id: 'NORT-12' }, panel: changesTab('northwind-delta') }),
  },
  asks: {
    about: 'Each ask: permission, question, plan review, and the three server items.',
    build: asks,
    screen: at({ panel: sessionTab(ASK_AGENT) }),
  },
  transcript: {
    about: 'A long session with every item type and every tool-call status.',
    build: transcript,
    screen: at({ panel: sessionTab('c3e8f1b5') }),
  },
  usage: { about: 'The seed with both usage windows read, one ahead of pace.', build: usage },
  devenv: {
    about: "The seed, with delta's session and its dev env tab side by side.",
    build: seedWorld,
    screen: at({ panel: sessionTab('e5b1d8c3') }),
    ui: {
      tabs: [sessionTab('e5b1d8c3'), devEnvTab('northwind-delta', 'web')],
      splitTabs: { 'northwind-delta': devEnvTab('northwind-delta', 'web').key },
    },
  },
  empty: { about: 'An empty desk, with no worktree.', build: empty },
  'host-down': { about: 'The agent host does not answer.', build: hostDown },
  comms: {
    about: 'Two open comms and a closed one, over tasks at each landing step.',
    build: comms,
    screen: at({ view: 'comms' }),
  },
  'long-text': {
    about: 'Titles, ids, a branch, a commit and a comm too long for a phone.',
    build: longText,
    screen: at({ view: 'list' }),
  },
} satisfies Record<string, Scenario>;

/** The URL a scenario opens on: the desk, for one whose first screen is the point. */
export const screenOf = (name: ScenarioName): string =>
  (SCENARIOS[name] as Scenario).screen ?? '/desk';

export type ScenarioName = keyof typeof SCENARIOS;

export function isScenarioName(name: string | null): name is ScenarioName {
  return name !== null && Object.hasOwn(SCENARIOS, name);
}
