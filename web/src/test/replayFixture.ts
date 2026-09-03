import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { contextForAgent, normaliseStreamEvent, type RawStreamEvent } from '../protocol/normalise';
import { applyEvent, initialClientState, type ClientState } from '../protocol/reducer';
import { makeAgent, worldWith } from './fixtures';

// vitest runs from web/, and the recorded daemon streams live beside the Python tests.
export const FIXTURES = resolve(process.cwd(), '../tests/fixtures/agent_events');

/** Where the golden replays live. The Python normaliser is held to these. */
export const GOLDEN = resolve(FIXTURES, 'normalised');

/** The clock every replay runs at, so ids and timestamps are stable. */
export const REPLAY_NOW = '2026-09-01T00:00:00Z';

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
    const out = normaliseStreamEvent(state, ctx, raw, REPLAY_NOW);
    ctx = out.ctx;
    for (const event of out.events) state = applyEvent(state, event);
  }
  return state;
}

/** The part of a replayed state the golden holds: the world and the transcripts. */
export function goldenOf(state: ClientState): {
  world: ClientState['world'];
  transcripts: ClientState['transcripts'];
} {
  return { world: state.world, transcripts: state.transcripts };
}

export function goldenPath(name: string): string {
  return resolve(GOLDEN, name.replace(/\.jsonl$/, '.json'));
}

/**
 * Write the golden for one fixture. Only runs under `UPDATE_GOLDEN=1`, so a
 * normaliser change is a deliberate re-record, never a silent drift.
 */
export function writeGolden(name: string): void {
  if (process.env.UPDATE_GOLDEN !== '1') return;
  mkdirSync(GOLDEN, { recursive: true });
  const text = JSON.stringify(goldenOf(replayFixture(name)), null, 2) + '\n';
  writeFileSync(goldenPath(name), text);
}

export function readGolden(name: string): unknown {
  return JSON.parse(readFileSync(goldenPath(name), 'utf8'));
}
