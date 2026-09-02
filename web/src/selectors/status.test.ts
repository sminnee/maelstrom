import { describe, expect, it } from 'vitest';
import { makeAgent, makeTask } from '../test/fixtures';
import { describeState } from './status';

describe('describeState', () => {
  it.each([
    ['a queued task that is not actionable', makeTask({ actionable: false }), undefined, 'Queued'],
    [
      'a queued task that is actionable',
      makeTask({ actionable: true }),
      undefined,
      'Ready to launch',
    ],
    ['a blocked task', makeTask({ status: 'blocked', actionable: false }), undefined, 'Blocked'],
    ['a working agent', makeTask(), makeAgent({ state: 'processing' }), 'Working'],
    ['an idle agent', makeTask(), makeAgent({ state: 'idle' }), 'Idle'],
    ['a question', makeTask(), makeAgent({ state: 'awaiting-question' }), 'Needs you · question'],
    [
      'a permission',
      makeTask(),
      makeAgent({ state: 'awaiting-permission' }),
      'Needs you · permission',
    ],
    [
      'a plan review',
      makeTask(),
      makeAgent({ state: 'awaiting-plan-review' }),
      'Needs you · plan review',
    ],
    ['a done task', makeTask({ status: 'done' }), makeAgent({ state: 'exited' }), 'Done'],
    ['a cancelled task', makeTask({ status: 'cancelled' }), undefined, 'Cancelled'],
    ['a clean exit', makeTask(), makeAgent({ state: 'exited', exitCode: 0 }), 'Exited'],
    ['a failed exit', makeTask(), makeAgent({ state: 'exited', exitCode: 1 }), 'Exited (code 1)'],
    [
      'an exit with no observed code',
      makeTask(),
      makeAgent({ state: 'exited', exitCode: null }),
      'Exited (unknown code)',
    ],
    ['an agent whose task is gone', undefined, makeAgent({ state: 'processing' }), 'Working'],
  ])('describes %s', (_, task, agent, expected) => {
    expect(describeState(task, agent)).toBe(expected);
  });
});
