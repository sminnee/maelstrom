import { describe, expect, it } from 'vitest';
import { applyEvent, initialClientState, type ClientState } from '../../protocol/reducer';
import type { ServerEvent } from '../../protocol/events';
import { seedWorld } from '../scenarios/seedWorld';
import { mulberry32 } from './rng';
import { initialSimState, step, type SimWorld } from './stepper';

function seeded(): ClientState {
  const seed = seedWorld();
  return applyEvent(initialClientState(), {
    type: 'snapshot',
    world: seed.world,
    transcripts: seed.transcripts,
  });
}

function run(seedValue: number, ticks: number) {
  let state = seeded();
  let sim: SimWorld = initialSimState(state);
  const rng = mulberry32(seedValue);
  const all: ServerEvent[] = [];
  for (let t = 0; t < ticks; t += 1) {
    const out = step(state, sim, rng, `2026-09-02T09:${String(t).padStart(2, '0')}:00Z`);
    sim = out.sim;
    for (const event of out.events) {
      state = applyEvent(state, event);
      all.push(event);
    }
  }
  return { state, events: all };
}

describe('the stepper', () => {
  it('produces identical events for the same seed', () => {
    expect(run(42, 60).events).toEqual(run(42, 60).events);
  });

  it('produces different events for different seeds', () => {
    expect(run(1, 60).events).not.toEqual(run(2, 60).events);
  });

  it('every emitted event applies through the reducer over many ticks', () => {
    expect(() => run(7, 300)).not.toThrow();
  });

  it('a wait raises exactly one open attention item for that agent', () => {
    const { state } = run(3, 120);
    for (const agent of Object.values(state.world.agents)) {
      const open = Object.values(state.world.attention).filter(
        (a) => a.clearedAt === null && a.agentId === agent.id,
      );
      if (agent.state.startsWith('awaiting-')) expect(open).toHaveLength(1);
      else expect(open.filter((a) => a.kind !== 'agent_exited')).toHaveLength(0);
    }
  });

  it('finish moves the task to done and unblocks its followers', () => {
    const { state } = run(5, 300);
    const done = Object.values(state.world.tasks).filter((t) => t.status === 'done');
    // The seed has two done tasks; the executing agents finish within the run.
    expect(done.length).toBeGreaterThan(2);
    // The finalising script has no waits, so NORT-12 finishes within the run;
    // NORT-15 was blocked behind it and must now be launched.
    expect(state.world.tasks['NORT-12']?.status).toBe('done');
    const follower = state.world.tasks['NORT-15']!;
    expect(['in-progress', 'done']).toContain(follower.status);
    expect(Object.values(state.world.agents).some((a) => a.taskId === 'NORT-15')).toBe(true);
  });

  it('parks a processing agent that asks, and skips it until answered', () => {
    let state = seeded();
    let sim = initialSimState(state);
    const rng = mulberry32(9);
    sim = { ...sim, force: [...sim.force, { kind: 'ask', agentId: 'd9a4c7f1' }] };
    let waited = false;
    for (let t = 0; t < 10 && !waited; t += 1) {
      const out = step(state, sim, rng, '2026-09-02T09:00:00Z');
      sim = out.sim;
      for (const e of out.events) state = applyEvent(state, e);
      waited = state.world.agents['d9a4c7f1']?.state === 'awaiting-question';
    }
    expect(waited).toBe(true);
    const before = state.transcripts['d9a4c7f1']?.items.length;
    const out = step(state, sim, rng, '2026-09-02T09:01:00Z');
    for (const e of out.events) state = applyEvent(state, e);
    expect(state.transcripts['d9a4c7f1']?.items.length).toBe(before);
  });
});
