import { describe, expect, it } from 'vitest';
import { finishedSubagentsOf, subagentsOf } from './agents';
import { makeAgent, worldWith } from '../test/fixtures';

describe('subagentsOf', () => {
  it("lists an agent's subagents by ordinal, and nobody else's", () => {
    const world = worldWith({
      agents: [
        makeAgent({ id: 'p1' }),
        makeAgent({ id: 'p1.10', parent: 'p1' }),
        makeAgent({ id: 'p1.2', parent: 'p1' }),
        makeAgent({ id: 'p1.1', parent: 'p1' }),
        makeAgent({ id: 'p1.1.1', parent: 'p1' }),
        makeAgent({ id: 'q1.1', parent: 'q1' }),
      ],
    });
    expect(subagentsOf(world, 'p1').map((a) => a.id)).toEqual(['p1.1', 'p1.1.1', 'p1.2', 'p1.10']);
    expect(subagentsOf(world, 'q1').map((a) => a.id)).toEqual(['q1.1']);
    expect(subagentsOf(world, 'p1.1')).toEqual([]);
  });

  it('lists the subagents that have not finished, whatever the exit code', () => {
    const world = worldWith({
      agents: [
        makeAgent({ id: 'p1' }),
        makeAgent({ id: 'p1.1', parent: 'p1', state: 'idle' }),
        makeAgent({ id: 'p1.2', parent: 'p1', state: 'awaiting-permission' }),
        makeAgent({ id: 'p1.3', parent: 'p1', state: 'processing' }),
        makeAgent({ id: 'p1.4', parent: 'p1', state: 'exited', exitCode: 0 }),
        makeAgent({ id: 'p1.5', parent: 'p1', state: 'exited', exitCode: 1 }),
      ],
    });
    expect(subagentsOf(world, 'p1').map((a) => a.id)).toEqual(['p1.1', 'p1.2', 'p1.3']);
  });
});

describe('finishedSubagentsOf', () => {
  it('lists the finished ones in the same order, and nobody else', () => {
    const world = worldWith({
      agents: [
        makeAgent({ id: 'p1' }),
        makeAgent({ id: 'p1.1', parent: 'p1' }),
        makeAgent({ id: 'p1.10', parent: 'p1', state: 'exited', exitCode: 0 }),
        makeAgent({ id: 'p1.2', parent: 'p1', state: 'exited', exitCode: 1 }),
        makeAgent({ id: 'q1.1', parent: 'q1', state: 'exited', exitCode: 0 }),
      ],
    });
    expect(finishedSubagentsOf(world, 'p1').map((a) => a.id)).toEqual(['p1.2', 'p1.10']);
    expect(finishedSubagentsOf(world, 'q1').map((a) => a.id)).toEqual(['q1.1']);
  });
});
