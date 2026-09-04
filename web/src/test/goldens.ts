import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { TranscriptItem } from '../protocol/transcript';

// vitest runs from web/; the goldens the Python normaliser owns live beside the Python tests.
const GOLDEN = resolve(process.cwd(), '../tests/fixtures/agent_events/normalised');

interface Golden {
  transcripts: Record<string, { items: TranscriptItem[] }>;
}

/**
 * The transcript items one recorded daemon stream normalises to, from the
 * golden `tests/test_orchestrator_normalise.py` owns. One fixture set feeds
 * both suites.
 */
export function goldenItems(fixture: string): TranscriptItem[] {
  const name = fixture.replace(/\.jsonl$/, '.json');
  const golden = JSON.parse(readFileSync(resolve(GOLDEN, name), 'utf8')) as Golden;
  return golden.transcripts['ag1']?.items ?? [];
}
