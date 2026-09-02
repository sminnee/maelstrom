import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { contextForAgent, normaliseStreamEvent, type RawStreamEvent } from '../protocol/normalise';
import { applyEvent, initialClientState, type ClientState } from '../protocol/reducer';
import { makeAgent, worldWith } from './fixtures';

// vitest runs from web/, and the recorded daemon streams live beside the Python tests.
export const FIXTURES = resolve(process.cwd(), '../tests/fixtures/agent_events');

export function readFixture(name: string): RawStreamEvent[] {
  return readFileSync(resolve(FIXTURES, name), 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line) as RawStreamEvent);
}

/** Replay a fixture through normalise and the reducer for agent `ag1`, as a real backend would. */
export function replayFixture(
  name: string,
  opts: { stopBeforeControlResponse?: boolean } = {},
): ClientState {
  const agent = makeAgent({ id: 'ag1', state: 'idle' });
  let state = applyEvent(initialClientState(), {
    type: 'snapshot',
    world: worldWith({ agents: [agent] }),
    transcripts: {},
  });
  let ctx = contextForAgent(state, 'ag1');
  for (const raw of readFixture(name)) {
    if (opts.stopBeforeControlResponse && raw.type === 'control_response' && ctx.pending) break;
    const out = normaliseStreamEvent(state, ctx, raw, '2026-09-01T00:00:00Z');
    ctx = out.ctx;
    for (const event of out.events) state = applyEvent(state, event);
  }
  return state;
}
