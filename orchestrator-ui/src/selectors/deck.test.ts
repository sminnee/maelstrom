import { describe, expect, it } from 'vitest';
import type { TaskStatus } from '../protocol/entities';
import { ZONES, type Zone } from '../protocol/progress';
import { deriveDeck, dropStatus, type DeckLane } from './deck';
import { noFilters } from './filters';
import { deriveGraph } from './graph';
import {
  makeAgent,
  makeAttention,
  makeTask,
  makeWorktree,
  onDesk,
  worldWith,
} from '../fake/fixtures';

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
    expect(deck.zones.todo.map((n) => n.id)).toEqual(['T1']);
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
    expect(deriveDeck(world, opts).counts).toEqual({ queued: 0, todo: 2, running: 0, done: 1 });
  });

  it('puts the nodes needing the user first in their zone, then the unanswered ones', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'T1', status: 'in-progress' }),
        makeTask({ id: 'T2', status: 'in-progress' }),
        makeTask({ id: 'T3', status: 'in-progress' }),
      ],
      agents: [
        makeAgent({ id: 'a1', taskId: 'T1', state: 'processing' }),
        makeAgent({ id: 'a2', taskId: 'T2', state: 'idle', lastMessage: 'Which default?' }),
        makeAgent({ id: 'a3', taskId: 'T3', state: 'awaiting-question' }),
      ],
      attention: [makeAttention({ taskId: 'T3', agentId: 'a3' })],
    });
    expect(deriveDeck(world, opts).zones.running.map((n) => n.id)).toEqual(['T3', 'T2', 'T1']);
  });

  it('draws the same nodes the canvas does, so the two surfaces cannot disagree', () => {
    const world = drawnWorld({
      tasks: [makeTask({ id: 'T1', status: 'todo' })],
      // A task the desk does not hold is not drawn, exactly as on the canvas.
      desk: [],
    });
    expect(deriveDeck(world, opts).counts).toEqual({ queued: 0, todo: 0, running: 0, done: 0 });
  });
});

describe('deriveDeck lanes', () => {
  /** A lane with its nodes as ids, so a test reads its whole shape. */
  const laneIds = (lane: DeckLane) => ({
    id: lane.id,
    label: lane.label,
    count: lane.count,
    zones: Object.fromEntries(ZONES.map((z) => [z, lane.zones[z].map((n) => n.id)])),
  });

  it('splits the zones into one lane per project, in id order, with work of two projects', () => {
    const world = drawnWorld({
      projects: [
        { id: 'northwind', name: 'Northwind', hasLinear: false },
        { id: 'aurora', name: 'Aurora', hasLinear: false },
      ],
      tasks: [
        makeTask({ id: 'N1', project: 'northwind', status: 'todo', actionable: true }),
        makeTask({ id: 'N2', project: 'northwind', status: 'done' }),
        makeTask({ id: 'A1', project: 'aurora', status: 'done' }),
      ],
    });
    expect(deriveDeck(world, opts).lanes.map(laneIds)).toEqual([
      {
        id: 'aurora',
        label: 'Aurora',
        count: 1,
        zones: { queued: [], todo: [], running: [], done: ['A1'] },
      },
      {
        id: 'northwind',
        label: 'Northwind',
        count: 2,
        zones: { queued: [], todo: ['N1'], running: [], done: ['N2'] },
      },
    ]);
  });

  it('draws no lanes with work of one project', () => {
    const world = drawnWorld({
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2', status: 'done' })],
    });
    expect(deriveDeck(world, opts).lanes).toEqual([]);
  });

  it('does not count a project whose only worktree is empty', () => {
    const world = drawnWorld({
      tasks: [makeTask({ id: 'T1', project: 'northwind' })],
      worktrees: [makeWorktree({ id: 'aurora-alpha', project: 'aurora', branch: 'feat/idea' })],
    });
    // The canvas draws the empty worktree in its own lane; the deck has no node for it.
    expect(deriveGraph(world, opts).groups.map((g) => g.id)).toEqual(['aurora', 'northwind']);
    expect(deriveDeck(world, opts).lanes).toEqual([]);
  });

  it('keeps the node needing the user first inside its lane', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'N1', project: 'northwind', status: 'in-progress' }),
        makeTask({ id: 'N2', project: 'northwind', status: 'in-progress' }),
        makeTask({ id: 'A1', project: 'aurora', status: 'done' }),
      ],
      agents: [
        makeAgent({ id: 'a1', taskId: 'N1', state: 'processing' }),
        makeAgent({ id: 'a2', taskId: 'N2', state: 'awaiting-question' }),
      ],
      attention: [makeAttention({ taskId: 'N2', agentId: 'a2' })],
    });
    const northwind = deriveDeck(world, opts).lanes.find((l) => l.id === 'northwind')!;
    expect(northwind.zones.running.map((n) => n.id)).toEqual(['N2', 'N1']);
  });
});

describe('dropStatus', () => {
  /** The drawn node of `id` in `world`, and the status a drop of it on `zone` sets. */
  function drop(world: ReturnType<typeof drawnWorld>, id: string, zone: Zone) {
    const deck = deriveDeck(world, opts);
    const node = Object.values(deck.zones)
      .flat()
      .find((n) => n.id === id)!;
    return dropStatus(node, zone, world.tasks);
  }

  const todo = makeTask({ id: 'T1', status: 'todo', actionable: true });
  const head = (status: TaskStatus) => makeTask({ id: 'H', status });
  const blocked = makeTask({ id: 'T2', status: 'blocked', actionable: false, follows: ['H'] });

  it.each<[string, Parameters<typeof drawnWorld>[0], string, Zone, TaskStatus | null]>([
    ['a to-do task dropped on Queued is blocked', { tasks: [todo] }, 'T1', 'queued', 'blocked'],
    ['any task dropped on Done is done', { tasks: [todo] }, 'T1', 'done', 'done'],
    ['a drop never launches', { tasks: [todo] }, 'T1', 'running', null],
    ['a drop on its own zone does nothing', { tasks: [todo] }, 'T1', 'todo', null],
    [
      'a queued todo dropped on Queued stays todo, as it does not move',
      {
        tasks: [
          head('in-progress'),
          makeTask({ id: 'T6', status: 'todo', actionable: false, follows: ['H'] }),
        ],
      },
      'T6',
      'queued',
      null,
    ],
    [
      'a blocked task whose follows are done goes back to todo',
      { tasks: [head('done'), blocked] },
      'T2',
      'todo',
      'todo',
    ],
    [
      'a blocked task whose follows are not done would stay queued, so To do refuses it',
      { tasks: [head('in-progress'), blocked] },
      'T2',
      'todo',
      null,
    ],
    [
      'a follows id the world does not hold is not done, as the server reads it',
      { tasks: [blocked] },
      'T2',
      'todo',
      null,
    ],
    [
      'a done task dropped on Queued is blocked',
      { tasks: [makeTask({ id: 'T3', status: 'done' })] },
      'T3',
      'queued',
      'blocked',
    ],
    [
      'a task with a live agent stays running, so To do refuses it',
      {
        tasks: [makeTask({ id: 'T4', status: 'in-progress' })],
        agents: [makeAgent({ taskId: 'T4', state: 'processing' })],
      },
      'T4',
      'todo',
      null,
    ],
  ])('%s', (_name, parts, id, zone, expected) => {
    expect(drop(drawnWorld(parts), id, zone)).toBe(expected);
  });

  it('a node an operation holds does not drag', () => {
    const world = drawnWorld({ tasks: [todo] });
    const node = { ...deriveDeck(world, opts).zones.todo[0]!, zone: 'running' as const };
    for (const zone of ZONES) expect(dropStatus(node, zone, world.tasks)).toBeNull();
  });

  it('a free agent does not drag', () => {
    const world = drawnWorld({ agents: [makeAgent({ id: 'free', taskId: '' })] });
    const deck = deriveDeck(world, opts);
    const node = Object.values(deck.zones).flat()[0]!;
    expect(node.kind).not.toBe('task');
    for (const zone of ZONES) expect(dropStatus(node, zone, world.tasks)).toBeNull();
  });
});
