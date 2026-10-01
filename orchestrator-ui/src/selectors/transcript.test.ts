import { describe, expect, it } from 'vitest';
import type { TranscriptItem } from '../protocol/transcript';
import { answeredOnCanvas, recentMessages } from './transcript';

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
  { id: 'q1', ts: '', type: 'question', requestId: 'req-1', questions: [] },
  said('m3', 'assistant', 'After the wait.'),
];

describe('the recent messages', () => {
  const ids = (opts?: Parameters<typeof recentMessages>[1]) =>
    recentMessages(items, opts).map((i) => i.id);

  it('returns the last three agent messages, in order', () => {
    const long = [said('m0', 'assistant', 'Starting.'), ...items];
    expect(recentMessages(long).map((i) => i.id)).toEqual(['m1', 'm2', 'm3']);
  });

  // `items` holds three tool calls and a user message among its messages.
  it('leaves out tool calls and what the user said', () => {
    expect(ids()).toEqual(['m1', 'm2', 'm3']);
  });

  it('stops at the wait when one is named', () => {
    expect(ids({ before: 'req-1' })).toEqual(['m1', 'm2']);
  });

  it('is empty when the named wait is unknown', () => {
    expect(ids({ before: 'req-9' })).toEqual([]);
  });
});

describe('presenting a wait', () => {
  describe('which surface answers it', () => {
    it('gives the wait to the expanded card when the card shows the waiting task', () => {
      expect(answeredOnCanvas('MAEL-52', 'MAEL-52')).toBe(true);
    });

    it('gives the wait to the panel when no card is expanded', () => {
      expect(answeredOnCanvas(null, 'MAEL-52')).toBe(false);
    });

    it('gives the wait to the panel when the card is expanded on another task', () => {
      expect(answeredOnCanvas('NORT-7', 'MAEL-52')).toBe(false);
    });

    // A free agent has no task, so it draws under its own id on both sides.
    it('gives the wait to the expanded card when the waiting agent has no task', () => {
      expect(answeredOnCanvas('d9a4c7f1', 'd9a4c7f1')).toBe(true);
    });
  });
});
