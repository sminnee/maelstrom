import { describe, expect, it } from 'vitest';
import type { TranscriptItem } from '../protocol/transcript';
import { makeQuestionItem } from '../test/fixtures';
import { contextBefore } from './transcript';

const said = (id: string, role: 'user' | 'assistant', markdown: string): TranscriptItem => ({
  id,
  ts: '',
  type: 'message',
  role,
  markdown,
});
const called = (id: string, tool: string): TranscriptItem => ({
  id,
  ts: '',
  type: 'tool_call',
  toolUseId: id,
  tool,
  input: {},
  status: 'done',
});

const items: TranscriptItem[] = [
  said('m1', 'assistant', 'Reading the model.'),
  called('t1', 'Read'),
  said('u1', 'user', 'Prefer streaming.'),
  said('m2', 'assistant', 'Two options are plausible.'),
  called('t2', 'Bash'),
  called('t3', 'AskUserQuestion'),
  makeQuestionItem({ id: 'q1' }),
  said('m3', 'assistant', 'After the wait.'),
];

describe('contextBefore', () => {
  it('returns the last n assistant messages and tool calls before the wait, in order', () => {
    expect(contextBefore(items, 'req-1', 2).map((i) => i.id)).toEqual(['m2', 't2']);
    expect(contextBefore(items, 'req-1', 3).map((i) => i.id)).toEqual(['t1', 'm2', 't2']);
  });

  it('skips the tool call that raised the wait and everything after it', () => {
    const ids = contextBefore(items, 'req-1', 10).map((i) => i.id);
    expect(ids).not.toContain('t3');
    expect(ids).not.toContain('m3');
    expect(ids).not.toContain('u1');
  });

  it('keeps a tool call before a wait that no tool call raised', () => {
    const bare: TranscriptItem[] = [
      said('m1', 'assistant', 'Reading the model.'),
      called('t1', 'Read'),
      makeQuestionItem({ id: 'q2', requestId: 'req-2' }),
    ];
    expect(contextBefore(bare, 'req-2').map((i) => i.id)).toEqual(['m1', 't1']);
  });

  it('is empty when the request is unknown', () => {
    expect(contextBefore(items, 'req-9')).toEqual([]);
  });
});
