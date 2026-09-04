import { describe, expect, it } from 'vitest';
import { isActionable, nodeState, phaseForCommand } from './phase';
import { makeAgent, makeAttention, makeTask } from '../test/fixtures';

describe('phaseForCommand', () => {
  it.each([
    ['shape', 'shape'],
    ['plan-task', 'plan'],
    ['plan-next-step', 'plan'],
    ['watch-pr', 'land'],
    // An execute task runs no skill, so an empty command is the ordinary build case.
    ['', 'build'],
  ])('%j → %s', (command, phase) => {
    expect(phaseForCommand(command)).toBe(phase);
  });

  // A command nobody recognises is not a build task: it is a task whose phase
  // is unknown, and the node draws no phase rather than claiming a wrong one.
  it.each(['some-other-skill', 'watch-prs', 'comand'])('%j has no phase', (command) => {
    expect(phaseForCommand(command)).toBeNull();
  });
});

describe('isActionable', () => {
  const done = makeTask({ id: 'A', status: 'done' });
  const open = makeTask({ id: 'B', status: 'in-progress' });

  it('is true for a todo task whose follows are all done', () => {
    const task = makeTask({ id: 'C', status: 'todo', follows: ['A'] });
    expect(isActionable(task, { A: done, B: open, C: task })).toBe(true);
  });

  it('is false while a followed task is not done', () => {
    const task = makeTask({ id: 'C', status: 'todo', follows: ['A', 'B'] });
    expect(isActionable(task, { A: done, B: open, C: task })).toBe(false);
  });

  it.each(['done', 'cancelled', 'blocked', 'template'] as const)('is false when %s', (status) => {
    const task = makeTask({ id: 'C', status });
    expect(isActionable(task, { C: task })).toBe(false);
  });
});

describe('nodeState', () => {
  it('is ready for an actionable task with no agent', () => {
    expect(nodeState(makeTask({ status: 'todo', actionable: true }), undefined, [])).toBe('ready');
  });

  it('is queued for a task whose turn has not come', () => {
    expect(nodeState(makeTask({ status: 'todo', actionable: false }), undefined, [])).toBe(
      'queued',
    );
  });

  // Cancelled is neither a success nor a fault: it is work that stopped.
  it('is cancelled for a cancelled task, apart from done', () => {
    expect(nodeState(makeTask({ status: 'cancelled' }), undefined, [])).toBe('cancelled');
    expect(nodeState(makeTask({ status: 'done' }), undefined, [])).toBe('done');
  });

  it('is working while the agent is processing', () => {
    expect(nodeState(makeTask({ status: 'in-progress' }), makeAgent(), [])).toBe('working');
  });

  it('is needs-attention while an open attention item points at the task', () => {
    const agent = makeAgent({ state: 'awaiting-plan-review' });
    expect(nodeState(makeTask({ status: 'in-progress' }), agent, [makeAttention()])).toBe(
      'needs-attention',
    );
  });

  it('ignores a cleared attention item', () => {
    const agent = makeAgent({ state: 'idle' });
    const cleared = makeAttention({ clearedAt: '2026-09-01T00:01:00Z' });
    expect(nodeState(makeTask({ status: 'in-progress' }), agent, [cleared])).toBe('idle');
  });

  it('is done once the task is done, whatever the agent says', () => {
    expect(nodeState(makeTask({ status: 'done' }), makeAgent(), [makeAttention()])).toBe('done');
  });

  it('is exited when the agent left with a non-zero code, even with attention open', () => {
    const agent = makeAgent({ state: 'exited', exitCode: 1 });
    expect(nodeState(makeTask({ status: 'in-progress' }), agent, [])).toBe('exited');
    const exited = makeAttention({ kind: 'agent_exited' });
    expect(nodeState(makeTask({ status: 'in-progress' }), agent, [exited])).toBe('exited');
    const unknown = makeAgent({ state: 'exited', exitCode: null });
    expect(nodeState(makeTask({ status: 'in-progress' }), unknown, [])).toBe('exited');
  });

  it('is idle when the agent exited cleanly', () => {
    const agent = makeAgent({ state: 'exited', exitCode: 0 });
    expect(nodeState(makeTask({ status: 'in-progress' }), agent, [])).toBe('idle');
  });

  it('a free agent takes its state from the agent alone', () => {
    expect(nodeState(undefined, makeAgent({ state: 'processing' }), [])).toBe('working');
    expect(nodeState(undefined, makeAgent({ state: 'exited', exitCode: 0 }), [])).toBe('idle');
    expect(nodeState(undefined, makeAgent({ state: 'exited', exitCode: 1 }), [])).toBe('exited');
  });

  it('a free agent with open attention needs the user', () => {
    const agent = makeAgent({ id: 'a1', state: 'awaiting-question' });
    const raised = makeAttention({ agentId: 'a1', taskId: '' });
    expect(nodeState(undefined, agent, [raised])).toBe('needs-attention');
  });
});
