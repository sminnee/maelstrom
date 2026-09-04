import { describe, expect, it } from 'vitest';
import { subagentsOf } from './agents';
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
});
