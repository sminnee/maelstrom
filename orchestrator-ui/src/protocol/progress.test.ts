import { describe, expect, it } from 'vitest';
import { makeAgent, makeAttention, makeTask } from '../fake/fixtures';
import type { Agent, AgentState, TaskStatus, WorktreeGate } from './entities';
import { progressOf, zoneForState, type DriftKind, type NodeState } from './progress';

/** The five positions of the agent axis, as the matrix names them. */
const AGENTS = {
  none: undefined,
  live: makeAgent({ state: 'processing' }),
  // An idle agent on the matrix has ended a turn, so it has said something.
  idle: makeAgent({ state: 'idle', lastMessage: 'Which default do you want?' }),
  finished: makeAgent({ state: 'exited', exitCode: 0 }),
  fault: makeAgent({ state: 'exited', exitCode: 1 }),
} satisfies Record<string, Agent | undefined>;

type AgentPosition = keyof typeof AGENTS;

interface Row {
  status: TaskStatus;
  actionable: boolean;
  agent: AgentPosition;
  state: NodeState;
  words: string;
  drift: DriftKind | null;
  fixStatus: TaskStatus | null;
}

const rows: Row[] = [
  // todo, actionable
  {
    status: 'todo',
    actionable: true,
    agent: 'none',
    state: 'ready',
    words: 'Ready to launch',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'todo',
    actionable: true,
    agent: 'live',
    state: 'working',
    words: 'Working',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'todo',
    actionable: true,
    agent: 'idle',
    state: 'unanswered',
    words: 'Unanswered',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  // An agent that ran and exited on a todo task is not a disagreement
  // `reconcile()` recognises: it walks in-progress tasks and live sessions only.
  {
    status: 'todo',
    actionable: true,
    agent: 'finished',
    state: 'stopped',
    words: 'Finished',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'todo',
    actionable: true,
    agent: 'fault',
    state: 'exited',
    words: 'Exited (code 1)',
    drift: null,
    fixStatus: null,
  },

  // todo, not actionable
  {
    status: 'todo',
    actionable: false,
    agent: 'none',
    state: 'queued',
    words: 'Queued',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'todo',
    actionable: false,
    agent: 'live',
    state: 'working',
    words: 'Working',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'todo',
    actionable: false,
    agent: 'idle',
    state: 'unanswered',
    words: 'Unanswered',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'todo',
    actionable: false,
    agent: 'finished',
    state: 'stopped',
    words: 'Finished',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'todo',
    actionable: false,
    agent: 'fault',
    state: 'exited',
    words: 'Exited (code 1)',
    drift: null,
    fixStatus: null,
  },

  // in-progress
  {
    status: 'in-progress',
    actionable: true,
    agent: 'none',
    state: 'ready',
    words: 'Ready to launch',
    drift: 'never-ran',
    fixStatus: null,
  },
  {
    status: 'in-progress',
    actionable: false,
    agent: 'none',
    state: 'queued',
    words: 'Queued',
    drift: 'never-ran',
    fixStatus: null,
  },
  {
    status: 'in-progress',
    actionable: true,
    agent: 'live',
    state: 'working',
    words: 'Working',
    drift: null,
    fixStatus: null,
  },
  // These two rows are the pair the board has to separate: a session whose
  // process is up and waiting at a prompt, against one that ended and can be
  // resumed.
  {
    status: 'in-progress',
    actionable: true,
    agent: 'idle',
    state: 'unanswered',
    words: 'Unanswered',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'in-progress',
    actionable: true,
    agent: 'finished',
    state: 'stopped',
    words: 'Finished',
    drift: 'finished',
    fixStatus: 'done',
  },
  {
    status: 'in-progress',
    actionable: true,
    agent: 'fault',
    state: 'exited',
    words: 'Exited (code 1)',
    drift: 'finished',
    fixStatus: 'done',
  },

  // blocked
  {
    status: 'blocked',
    actionable: false,
    agent: 'none',
    state: 'queued',
    words: 'Blocked',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'blocked',
    actionable: false,
    agent: 'live',
    state: 'working',
    words: 'Working',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'blocked',
    actionable: false,
    agent: 'idle',
    state: 'unanswered',
    words: 'Unanswered',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'blocked',
    actionable: false,
    agent: 'finished',
    state: 'stopped',
    words: 'Finished',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'blocked',
    actionable: false,
    agent: 'fault',
    state: 'exited',
    words: 'Exited (code 1)',
    drift: null,
    fixStatus: null,
  },

  // done — the PR is pushed and watch-pr carries it to green, so a live agent
  // on a closed task is finalising rather than drift. A stopped one is history.
  {
    status: 'done',
    actionable: false,
    agent: 'none',
    state: 'done',
    words: 'Done',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'done',
    actionable: false,
    agent: 'live',
    state: 'finalising',
    words: 'Finalising',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'done',
    actionable: false,
    agent: 'idle',
    state: 'done',
    words: 'Done',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'done',
    actionable: false,
    agent: 'finished',
    state: 'done',
    words: 'Done',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'done',
    actionable: false,
    agent: 'fault',
    state: 'done',
    words: 'Done',
    drift: null,
    fixStatus: null,
  },

  // cancelled
  {
    status: 'cancelled',
    actionable: false,
    agent: 'none',
    state: 'cancelled',
    words: 'Cancelled',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'cancelled',
    actionable: false,
    agent: 'live',
    state: 'finalising',
    words: 'Finalising',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'cancelled',
    actionable: false,
    agent: 'idle',
    state: 'cancelled',
    words: 'Cancelled',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'cancelled',
    actionable: false,
    agent: 'finished',
    state: 'cancelled',
    words: 'Cancelled',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'cancelled',
    actionable: false,
    agent: 'fault',
    state: 'cancelled',
    words: 'Cancelled',
    drift: null,
    fixStatus: null,
  },

  // template — never reaches the canvas, but the task list shows it.
  {
    status: 'template',
    actionable: false,
    agent: 'none',
    state: 'queued',
    words: 'Template',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'template',
    actionable: false,
    agent: 'live',
    state: 'working',
    words: 'Working',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'template',
    actionable: false,
    agent: 'idle',
    state: 'unanswered',
    words: 'Unanswered',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'template',
    actionable: false,
    agent: 'finished',
    state: 'stopped',
    words: 'Finished',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'template',
    actionable: false,
    agent: 'fault',
    state: 'exited',
    words: 'Exited (code 1)',
    drift: null,
    fixStatus: null,
  },
];

describe('progressOf', () => {
  it.each(rows)(
    '$status ($actionable) with $agent agent reads $state / $words / $drift',
    ({ status, actionable, agent, state, words, drift, fixStatus }) => {
      const task = makeTask({ status, actionable });
      expect(progressOf(task, AGENTS[agent], [], null)).toMatchObject({
        state,
        words,
        drift,
        fixStatus,
      });
    },
  );

  it('names the wait an awaiting agent is on, when attention is open', () => {
    const waits: [AgentState, string][] = [
      ['awaiting-question', 'Needs you · question'],
      ['awaiting-permission', 'Needs you · permission'],
      ['awaiting-plan-review', 'Needs you · plan review'],
    ];
    for (const [state, words] of waits) {
      const agent = makeAgent({ state });
      const progress = progressOf(
        makeTask({ status: 'in-progress' }),
        agent,
        [makeAttention()],
        null,
      );
      expect(progress).toMatchObject({ state: 'needs-attention', words });
    }
  });

  it('counts the asks when an agent is blocked on several at once', () => {
    // One state cannot name two waits, and naming only the newest hides the
    // rest. See `docs/dev/agent-daemon.md`, "A subagent's permission ask".
    const agent = makeAgent({
      state: 'awaiting-permission',
      pendingRequestIds: ['req-1', 'req-2'],
    });
    const progress = progressOf(
      makeTask({ status: 'in-progress' }),
      agent,
      [makeAttention()],
      null,
    );
    expect(progress).toMatchObject({ state: 'needs-attention', words: 'Needs you · 2 asks' });
  });

  // The bug this collapse exists to fix: the two readings agreed only by
  // accident. Now "Needs you" appears exactly when the state does.
  it('reads an awaiting agent as idle when no attention item is open', () => {
    const agent = makeAgent({ state: 'awaiting-question', lastMessage: 'Which default?' });
    expect(progressOf(makeTask({ status: 'in-progress' }), agent, [], null)).toMatchObject({
      state: 'idle',
      words: 'Idle',
    });
  });

  it('reads an idle agent that has said nothing as idle', () => {
    const agent = makeAgent({ state: 'idle', lastMessage: '' });
    expect(progressOf(makeTask({ status: 'in-progress' }), agent, [], null)).toMatchObject({
      state: 'idle',
      words: 'Idle',
    });
  });

  // A free agent has no task to finish, so its last message asks for nothing.
  it('reads an idle free agent as idle, whatever it said', () => {
    expect(progressOf(undefined, AGENTS.idle, [], null)).toMatchObject({
      state: 'idle',
      words: 'Idle',
    });
  });

  it('lets an open attention item outrank an unanswered message', () => {
    const progress = progressOf(
      makeTask({ status: 'in-progress' }),
      AGENTS.idle,
      [makeAttention()],
      null,
    );
    expect(progress).toMatchObject({ state: 'needs-attention' });
  });

  it('ignores a cleared attention item', () => {
    const agent = makeAgent({ state: 'awaiting-question' });
    const cleared = makeAttention({ clearedAt: '2026-09-01T00:01:00Z' });
    expect(progressOf(makeTask({ status: 'in-progress' }), agent, [cleared], null)).toMatchObject({
      state: 'idle',
    });
  });

  it('lets a faulted agent outrank an open attention item', () => {
    const agent = makeAgent({ state: 'exited', exitCode: 1 });
    const raised = makeAttention({ kind: 'agent_exited' });
    expect(progressOf(makeTask({ status: 'in-progress' }), agent, [raised], null)).toMatchObject({
      state: 'exited',
    });
  });

  // watch-pr can still stop to ask, so a finalising agent reaches the user
  // like any other. It is the happy path, so it never drifts.
  it('lets a finalising agent still ask for the user', () => {
    const agent = makeAgent({ state: 'awaiting-question' });
    expect(progressOf(makeTask({ status: 'done' }), agent, [makeAttention()], null)).toMatchObject({
      state: 'needs-attention',
      words: 'Needs you · question',
      drift: null,
      fixStatus: null,
    });
  });

  it('lets a done task outrank a stopped agent', () => {
    const agent = makeAgent({ state: 'exited', exitCode: 1 });
    expect(progressOf(makeTask({ status: 'done' }), agent, [makeAttention()], null)).toMatchObject({
      state: 'done',
      words: 'Done',
      drift: null,
    });
  });

  it('says an awaiting agent needs the user wherever its attention item is passed', () => {
    const agent = makeAgent({ state: 'awaiting-permission' });
    const task = makeTask({ status: 'in-progress' });
    expect(progressOf(task, agent, [makeAttention()], null).words).toBe('Needs you · permission');
  });

  it('reads a free agent from the agent alone, and never drifts', () => {
    const raised = makeAttention({ agentId: 'agent-1', taskId: '' });
    expect(progressOf(undefined, makeAgent({ state: 'processing' }), [], null)).toMatchObject({
      state: 'working',
      words: 'Working',
      drift: null,
      fixStatus: null,
    });
    expect(
      progressOf(undefined, makeAgent({ state: 'exited', exitCode: 0 }), [], null),
    ).toMatchObject({
      state: 'stopped',
      words: 'Finished',
      drift: null,
    });
    expect(
      progressOf(undefined, makeAgent({ state: 'awaiting-question' }), [raised], null),
    ).toMatchObject({ state: 'needs-attention', drift: null });
  });

  it.each([
    ['delegating', 'Subagents working'],
    ['background', 'Background tasks working'],
  ] as const)('draws a %s agent as working, in the running zone', (state, words) => {
    const agent = makeAgent({ state });
    const progress = progressOf(makeTask({ status: 'in-progress' }), agent, [], null);
    expect(progress).toMatchObject({ state: 'working', words, drift: null });
    expect(zoneForState(progress.state)).toBe('running');
    // On a done task the work after the turn is its tail, not an orphan.
    expect(progressOf(makeTask({ status: 'done' }), agent, [], null)).toMatchObject({
      state: 'finalising',
      drift: null,
    });
  });

  describe('with a gate job on the worktree', () => {
    const running: WorktreeGate = {
      jobId: '43',
      state: 'running',
      gate: 'test',
      queuePosition: null,
    };
    const queued: WorktreeGate = { jobId: '44', state: 'queued', gate: null, queuePosition: 2 };
    const task = makeTask({ status: 'in-progress' });

    it.each([
      ['has said nothing', makeAgent({ state: 'idle', lastMessage: '' })],
      ['ended its turn on a message', AGENTS.idle],
    ])('reads an idle agent that %s as working while the gate runs', (_, agent) => {
      expect(progressOf(task, agent, [], running)).toMatchObject({
        state: 'working',
        words: 'Gate running',
      });
    });

    it.each([
      [queued, 'Gate queued'],
      [{ ...running, state: 'orphaned' } as const, 'Gate orphaned'],
    ])('names the state of the gate job', (gate, words) => {
      expect(progressOf(task, AGENTS.idle, [], gate).words).toBe(words);
    });

    it('lets a stopped agent outrank the gate', () => {
      expect(progressOf(task, AGENTS.finished, [], running).state).toBe('stopped');
    });

    it('lets an open attention item outrank the gate', () => {
      const agent = makeAgent({ state: 'awaiting-question' });
      expect(progressOf(task, agent, [makeAttention()], running).state).toBe('needs-attention');
    });

    it('keeps the words of an agent whose own turn runs', () => {
      expect(progressOf(task, AGENTS.live, [], running).words).toBe('Working');
    });
  });

  it('does not draw an unobserved exit as a fault', () => {
    const agent = makeAgent({ state: 'exited', exitCode: null });
    expect(progressOf(makeTask({ status: 'in-progress' }), agent, [], null)).toMatchObject({
      state: 'stopped',
      // Not "Finished": nobody saw this agent finish, so the words must not
      // claim the work completed while offering to mark the task done.
      words: 'Stopped',
      drift: 'finished',
    });
  });
});

describe('echoesStatus', () => {
  it.each([
    ['a done task', makeTask({ status: 'done' }), undefined, true],
    ['a cancelled task', makeTask({ status: 'cancelled' }), undefined, true],
    [
      'a blocked task with no agent',
      makeTask({ status: 'blocked', actionable: false }),
      undefined,
      true,
    ],
    ['a queued task', makeTask({ status: 'todo', actionable: false }), undefined, false],
    ['a working agent', makeTask({ status: 'in-progress' }), AGENTS.live, false],
  ] as const)('is %s → %s', (_, task, agent, expected) => {
    expect(progressOf(task, agent, [], null).echoesStatus).toBe(expected);
  });

  // A status control must never speak alone about a value the card disputes.
  it('is false whenever the status is drifting', () => {
    expect(progressOf(makeTask({ status: 'blocked' }), AGENTS.idle, [], null).echoesStatus).toBe(
      false,
    );
    expect(
      progressOf(makeTask({ status: 'in-progress' }), AGENTS.finished, [], null).echoesStatus,
    ).toBe(false);
  });

  // The card says "Finalising"; the picker saying "done" alone would hide the
  // fact that an agent is still carrying the PR.
  it('is false while a closed task is still finalising', () => {
    expect(progressOf(makeTask({ status: 'done' }), AGENTS.live, [], null).echoesStatus).toBe(
      false,
    );
  });

  it('is false for a free agent, which has no status to echo', () => {
    expect(progressOf(undefined, AGENTS.live, [], null).echoesStatus).toBe(false);
  });
});

describe('zoneForState', () => {
  it.each([
    ['done', 'done'],
    // Cancelled is terminal history, and not running.
    ['cancelled', 'done'],
    ['working', 'running'],
    ['needs-attention', 'running'],
    ['idle', 'running'],
    // A stopped session is resumable, so it is unfinished work in the running
    // zone rather than history.
    ['stopped', 'running'],
    // Work is not settled until CI is green, so finalising stays out of done.
    ['finalising', 'running'],
    // A run that stopped without the task being marked done is unfinished
    // work, not history.
    ['exited', 'running'],
    ['ready', 'notStarted'],
    ['queued', 'notStarted'],
  ] as const)('%s sits in the %s zone', (state, zone) => {
    expect(zoneForState(state)).toBe(zone);
  });
});
