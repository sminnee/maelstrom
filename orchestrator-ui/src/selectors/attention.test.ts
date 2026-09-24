import { describe, expect, it } from 'vitest';
import { nextAttentionTask, openAttention } from './attention';
import { makeAttention, worldWith } from '../test/fixtures';

const world = worldWith({
  attention: [
    makeAttention({
      id: 'q-old',
      kind: 'question',
      taskId: 'T1',
      raisedAt: '2026-09-01T00:00:01Z',
    }),
    makeAttention({
      id: 'p-new',
      kind: 'plan_review',
      taskId: 'T2',
      raisedAt: '2026-09-01T00:00:09Z',
    }),
    makeAttention({
      id: 'perm',
      kind: 'permission',
      taskId: 'T3',
      raisedAt: '2026-09-01T00:00:02Z',
    }),
    makeAttention({
      id: 'p-old',
      kind: 'plan_review',
      taskId: 'T4',
      raisedAt: '2026-09-01T00:00:03Z',
    }),
    makeAttention({
      id: 'gone',
      kind: 'question',
      taskId: 'T5',
      clearedAt: '2026-09-01T00:01:00Z',
    }),
    makeAttention({
      id: 'exit',
      kind: 'agent_exited',
      taskId: 'T6',
      raisedAt: '2026-09-01T00:00:00Z',
    }),
  ],
});

describe('openAttention', () => {
  it('orders plan reviews, then questions, then permissions, then the rest, oldest first', () => {
    expect(openAttention(world).map((a) => a.id)).toEqual([
      'p-old',
      'p-new',
      'q-old',
      'perm',
      'exit',
    ]);
  });
});

describe('nextAttentionTask', () => {
  it('starts at the top and cycles through the open items', () => {
    expect(nextAttentionTask(world, null)).toBe('T4');
    expect(nextAttentionTask(world, 'T4')).toBe('T2');
    expect(nextAttentionTask(world, 'T6')).toBe('T4');
  });

  it('skips items on tasks the canvas does not show', () => {
    const visible = new Set(['T2', 'T6']);
    expect(openAttention(world, visible).map((a) => a.id)).toEqual(['p-new', 'exit']);
    expect(nextAttentionTask(world, 'T2', visible)).toBe('T6');
  });

  it('is null when nothing is open', () => {
    expect(nextAttentionTask(worldWith({}), null)).toBeNull();
  });
});
