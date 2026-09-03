import { describe, expect, it } from 'vitest';
import { applyServerEvent, initialClientState } from './reducer';
import type { EventFrame } from './events';
import { makeAgent, makeTask, worldWith } from '../test/fixtures';
import { emptyWorld } from './reducer';

function frame(seq: number, event: EventFrame['event']): EventFrame {
  return { seq, ts: '2026-09-01T00:00:00Z', event };
}

describe('applyServerEvent', () => {
  it('a snapshot replaces the world and transcripts and records its seq', () => {
    const world = worldWith({ tasks: [makeTask()] });
    const state = applyServerEvent(
      initialClientState(),
      frame(1, {
        type: 'snapshot',
        world,
        transcripts: { 'agent-1': { agentId: 'agent-1', items: [], truncatedBefore: false } },
      }),
    );
    expect(state.world.tasks['NORT-7']?.title).toBe('Add order export');
    expect(state.transcripts['agent-1']?.items).toEqual([]);
    expect(state.lastSeq).toBe(1);
  });

  it('an upsert adds a new entity and replaces an existing one whole', () => {
    let state = applyServerEvent(
      initialClientState(),
      frame(1, { type: 'snapshot', world: emptyWorld(), transcripts: {} }),
    );
    state = applyServerEvent(state, frame(2, { type: 'upsert', kind: 'task', entity: makeTask() }));
    expect(state.world.tasks['NORT-7']?.status).toBe('todo');
    state = applyServerEvent(
      state,
      frame(3, { type: 'upsert', kind: 'task', entity: makeTask({ status: 'done' }) }),
    );
    expect(state.world.tasks['NORT-7']?.status).toBe('done');
    state = applyServerEvent(
      state,
      frame(4, { type: 'upsert', kind: 'agent', entity: makeAgent() }),
    );
    expect(state.world.agents['agent-1']?.state).toBe('processing');
  });

  it('drops a frame whose seq is not newer than the last one applied', () => {
    let state = applyServerEvent(
      initialClientState(),
      frame(5, { type: 'snapshot', world: emptyWorld(), transcripts: {} }),
    );
    state = applyServerEvent(state, frame(5, { type: 'upsert', kind: 'task', entity: makeTask() }));
    state = applyServerEvent(state, frame(3, { type: 'upsert', kind: 'task', entity: makeTask() }));
    expect(state.world.tasks).toEqual({});
    expect(state.lastSeq).toBe(5);
  });

  it('a snapshot is a new epoch: it lands whatever its seq and resets the guard', () => {
    let state = applyServerEvent(
      initialClientState(),
      frame(500, { type: 'snapshot', world: emptyWorld(), transcripts: {} }),
    );
    state = applyServerEvent(
      state,
      frame(1, { type: 'snapshot', world: worldWith({ tasks: [makeTask()] }), transcripts: {} }),
    );
    expect(state.world.tasks['NORT-7']).toBeDefined();
    expect(state.lastSeq).toBe(1);
    state = applyServerEvent(state, frame(2, { type: 'remove', kind: 'task', id: 'NORT-7' }));
    expect(state.world.tasks).toEqual({});
  });

  it('remove deletes the entity by kind and id', () => {
    let state = applyServerEvent(
      initialClientState(),
      frame(1, {
        type: 'snapshot',
        world: worldWith({ tasks: [makeTask()], agents: [makeAgent()] }),
        transcripts: {},
      }),
    );
    state = applyServerEvent(state, frame(2, { type: 'remove', kind: 'agent', id: 'agent-1' }));
    expect(state.world.agents).toEqual({});
    expect(state.world.tasks['NORT-7']).toBeDefined();
  });

  it('transcript.append then transcript.update merges the patch into the item', () => {
    let state = applyServerEvent(
      initialClientState(),
      frame(1, { type: 'snapshot', world: emptyWorld(), transcripts: {} }),
    );
    state = applyServerEvent(
      state,
      frame(2, {
        type: 'transcript.append',
        agentId: 'agent-1',
        item: {
          id: 'toolu_1',
          ts: '2026-09-01T00:00:01Z',
          type: 'tool_call',
          toolUseId: 'toolu_1',
          tool: 'Bash',
          input: { command: 'ls' },
          status: 'running',
        },
      }),
    );
    state = applyServerEvent(
      state,
      frame(3, {
        type: 'transcript.update',
        agentId: 'agent-1',
        itemId: 'toolu_1',
        patch: { status: 'done', output: 'a.txt\n' },
      }),
    );
    const items = state.transcripts['agent-1']?.items ?? [];
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ tool: 'Bash', status: 'done', output: 'a.txt\n' });
  });

  it('transcript.truncated marks the window, even before any item', () => {
    let state = applyServerEvent(
      initialClientState(),
      frame(1, { type: 'snapshot', world: emptyWorld(), transcripts: {} }),
    );
    state = applyServerEvent(state, frame(2, { type: 'transcript.truncated', agentId: 'agent-1' }));
    expect(state.transcripts['agent-1']).toEqual({
      agentId: 'agent-1',
      items: [],
      truncatedBefore: true,
    });
  });

  it('rejects an upsert of an unknown entity kind', () => {
    const state = applyServerEvent(
      initialClientState(),
      frame(1, { type: 'snapshot', world: emptyWorld(), transcripts: {} }),
    );
    const bad = {
      type: 'upsert',
      kind: 'widget',
      entity: { id: 'w' },
    } as unknown as EventFrame['event'];
    expect(() => applyServerEvent(state, frame(2, bad))).toThrow(/widget/);
  });
});
