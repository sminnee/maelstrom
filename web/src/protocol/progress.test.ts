import { describe, expect, it } from 'vitest';
import { makeAgent, makeAttention, makeTask } from '../test/fixtures';
import type { Agent, AgentState, TaskStatus } from './entities';
import { progressOf, zoneForState, type DriftKind, type NodeState } from './progress';

/** The five positions of the agent axis, as the matrix names them. */
const AGENTS = {
  none: undefined,
  live: makeAgent({ state: 'processing' }),
  idle: makeAgent({ state: 'idle' }),
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
    state: 'idle',
    words: 'Idle',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  // An agent that ran and exited on a todo task is not a disagreement
  // `reconcile()` recognises: it walks in-progress tasks and live sessions only.
  {
    status: 'todo',
    actionable: true,
    agent: 'finished',
    state: 'idle',
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
    state: 'idle',
    words: 'Idle',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'todo',
    actionable: false,
    agent: 'finished',
    state: 'idle',
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
  {
    status: 'in-progress',
    actionable: true,
    agent: 'idle',
    state: 'idle',
    words: 'Idle',
    drift: null,
    fixStatus: null,
  },
  {
    status: 'in-progress',
    actionable: true,
    agent: 'finished',
    state: 'idle',
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
    state: 'idle',
    words: 'Idle',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'blocked',
    actionable: false,
    agent: 'finished',
    state: 'idle',
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
    state: 'idle',
    words: 'Idle',
    drift: 'orphan-session',
    fixStatus: 'in-progress',
  },
  {
    status: 'template',
    actionable: false,
    agent: 'finished',
    state: 'idle',
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
      expect(progressOf(task, AGENTS[agent], [])).toMatchObject({ state, words, drift, fixStatus });
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
      const progress = progressOf(makeTask({ status: 'in-progress' }), agent, [makeAttention()]);
      expect(progress).toMatchObject({ state: 'needs-attention', words });
    }
  });

  // The bug this collapse exists to fix: the two readings agreed only by
  // accident. Now "Needs you" appears exactly when the state does.
  it('reads an awaiting agent as idle when no attention item is open', () => {
    const agent = makeAgent({ state: 'awaiting-question' });
    expect(progressOf(makeTask({ status: 'in-progress' }), agent, [])).toMatchObject({
      state: 'idle',
      words: 'Idle',
    });
  });

  it('ignores a cleared attention item', () => {
    const agent = makeAgent({ state: 'awaiting-question' });
    const cleared = makeAttention({ clearedAt: '2026-09-01T00:01:00Z' });
    expect(progressOf(makeTask({ status: 'in-progress' }), agent, [cleared])).toMatchObject({
      state: 'idle',
    });
  });

  it('lets a faulted agent outrank an open attention item', () => {
    const agent = makeAgent({ state: 'exited', exitCode: 1 });
    const raised = makeAttention({ kind: 'agent_exited' });
    expect(progressOf(makeTask({ status: 'in-progress' }), agent, [raised])).toMatchObject({
      state: 'exited',
    });
  });

  // watch-pr can still stop to ask, so a finalising agent reaches the user
  // like any other. It is the happy path, so it never drifts.
  it('lets a finalising agent still ask for the user', () => {
    const agent = makeAgent({ state: 'awaiting-question' });
    expect(progressOf(makeTask({ status: 'done' }), agent, [makeAttention()])).toMatchObject({
      state: 'needs-attention',
      words: 'Needs you · question',
      drift: null,
      fixStatus: null,
    });
  });

  it('lets a done task outrank a stopped agent', () => {
    const agent = makeAgent({ state: 'exited', exitCode: 1 });
    expect(progressOf(makeTask({ status: 'done' }), agent, [makeAttention()])).toMatchObject({
      state: 'done',
      words: 'Done',
      drift: null,
    });
  });

  it('says an awaiting agent needs the user wherever its attention item is passed', () => {
    const agent = makeAgent({ state: 'awaiting-permission' });
    const task = makeTask({ status: 'in-progress' });
    expect(progressOf(task, agent, [makeAttention()]).words).toBe('Needs you · permission');
  });

  it('reads a free agent from the agent alone, and never drifts', () => {
    const raised = makeAttention({ agentId: 'agent-1', taskId: '' });
    expect(progressOf(undefined, makeAgent({ state: 'processing' }), [])).toMatchObject({
      state: 'working',
      words: 'Working',
      drift: null,
      fixStatus: null,
    });
    expect(progressOf(undefined, makeAgent({ state: 'exited', exitCode: 0 }), [])).toMatchObject({
      state: 'idle',
      words: 'Finished',
      drift: null,
    });
    expect(
      progressOf(undefined, makeAgent({ state: 'awaiting-question' }), [raised]),
    ).toMatchObject({ state: 'needs-attention', drift: null });
  });

  it('says an unobserved exit code is not a clean exit', () => {
    const agent = makeAgent({ state: 'exited', exitCode: null });
    expect(progressOf(makeTask({ status: 'in-progress' }), agent, [])).toMatchObject({
      state: 'exited',
      words: 'Exited (unknown code)',
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
    expect(progressOf(task, agent, []).echoesStatus).toBe(expected);
  });

  // A status control must never speak alone about a value the card disputes.
  it('is false whenever the status is drifting', () => {
    expect(progressOf(makeTask({ status: 'blocked' }), AGENTS.idle, []).echoesStatus).toBe(false);
    expect(progressOf(makeTask({ status: 'in-progress' }), AGENTS.finished, []).echoesStatus).toBe(
      false,
    );
  });

  // The card says "Finalising"; the picker saying "done" alone would hide the
  // fact that an agent is still carrying the PR.
  it('is false while a closed task is still finalising', () => {
    expect(progressOf(makeTask({ status: 'done' }), AGENTS.live, []).echoesStatus).toBe(false);
  });

  it('is false for a free agent, which has no status to echo', () => {
    expect(progressOf(undefined, AGENTS.live, []).echoesStatus).toBe(false);
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
