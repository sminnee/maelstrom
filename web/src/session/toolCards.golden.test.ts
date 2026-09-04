import { describe, expect, it } from 'vitest';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { classifyToolCall, toolCallTitle } from './toolCards';
import type { ToolCallItem } from '../protocol/transcript';

/**
 * The golden that ties `agent_view.classify_tool_call` / `tool_call_title` to
 * this module. Both are hand ports, and nothing else stops them drifting —
 * so this file is the reference and Python replays it, the same regime the
 * normaliser pair uses. `UPDATE_GOLDEN=1 pnpm test` re-records.
 */
const GOLDEN = resolve(
  __dirname,
  '../../../tests/fixtures/agent_events/normalised/tool-cards.json',
);

const CASES: [string, Record<string, unknown>][] = [
  ['Bash', { description: 'List files', command: 'ls -la' }],
  ['Bash', { command: 'ls -la' }],
  ['Bash', {}],
  ['Edit', { file_path: '/tmp/a.py', old_string: 'a', new_string: 'b' }],
  ['Write', { file_path: '/tmp/b.py', content: 'x' }],
  ['Read', { file_path: '/tmp/c.py' }],
  ['WebFetch', { url: 'https://example.com', prompt: 'what is this' }],
  ['WebSearch', { query: 'bicycles' }],
  ['Task', { description: 'go and look' }],
  ['ExitPlanMode', {}],
  ['AskUserQuestion', { questions: [] }],
];

const call = (tool: string, input: Record<string, unknown>): ToolCallItem => ({
  id: 't',
  ts: '',
  type: 'tool_call',
  toolUseId: 't',
  tool,
  input,
  status: 'done',
});

const rows = CASES.map(([tool, input]) => ({
  tool,
  input,
  kind: classifyToolCall(call(tool, input)),
  title: toolCallTitle(call(tool, input)),
}));

describe('tool card golden', () => {
  it('matches the recorded classification and titles', () => {
    if (process.env.UPDATE_GOLDEN === '1') {
      mkdirSync(dirname(GOLDEN), { recursive: true });
      writeFileSync(GOLDEN, JSON.stringify(rows, null, 2) + '\n');
    }
    expect(rows).toEqual(JSON.parse(readFileSync(GOLDEN, 'utf8')));
  });
});
