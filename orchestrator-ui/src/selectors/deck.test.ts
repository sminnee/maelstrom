import { describe, expect, it } from 'vitest';
import { deriveDeck } from './deck';
import { noFilters } from './filters';
import { makeAgent, makeAttention, makeTask, onDesk, worldWith } from '../test/fixtures';

/** A world whose every task is on the desk: what the deck list draws. */
function drawnWorld(parts: Parameters<typeof worldWith>[0]) {
  return worldWith({ ...parts, desk: parts.desk ?? onDesk(parts.tasks ?? []) });
}

const opts = { filters: noFilters() };

describe('deriveDeck', () => {
  it('buckets each node into the zone its state falls in', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'T1', status: 'todo', actionable: true }),
        makeTask({ id: 'T2', status: 'in-progress' }),
        makeTask({ id: 'T3', status: 'done' }),
      ],
      agents: [makeAgent({ taskId: 'T2', state: 'processing' })],
    });
    const deck = deriveDeck(world, opts);
    expect(deck.zones.notStarted.map((n) => n.id)).toEqual(['T1']);
    expect(deck.zones.running.map((n) => n.id)).toEqual(['T2']);
    expect(deck.zones.done.map((n) => n.id)).toEqual(['T3']);
  });

  it('counts each zone, so a tab can say how much is in it', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'T1', status: 'todo' }),
        makeTask({ id: 'T2', status: 'todo' }),
        makeTask({ id: 'T3', status: 'done' }),
      ],
    });
    expect(deriveDeck(world, opts).counts).toEqual({ done: 1, running: 0, notStarted: 2 });
  });

  it('puts the nodes needing the user first in their zone, so the phone opens on the ask', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'T1', status: 'in-progress' }),
        makeTask({ id: 'T2', status: 'in-progress' }),
      ],
      agents: [
        makeAgent({ id: 'a1', taskId: 'T1', state: 'processing' }),
        makeAgent({ id: 'a2', taskId: 'T2', state: 'awaiting-question' }),
      ],
      attention: [makeAttention({ taskId: 'T2', agentId: 'a2' })],
    });
    expect(deriveDeck(world, opts).zones.running.map((n) => n.id)).toEqual(['T2', 'T1']);
  });

  it('draws the same nodes the canvas does, so the two surfaces cannot disagree', () => {
    const world = drawnWorld({
      tasks: [makeTask({ id: 'T1', status: 'todo' })],
      // A task the desk does not hold is not drawn, exactly as on the canvas.
      desk: [],
    });
    expect(deriveDeck(world, opts).counts).toEqual({ done: 0, running: 0, notStarted: 0 });
  });
});
