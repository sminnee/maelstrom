import { describe, expect, it } from 'vitest';
import { editToDiffRows } from './diffRows';

describe('editToDiffRows', () => {
  it('marks removed, added and unchanged lines', () => {
    const rows = editToDiffRows('a\nb\nc\n', 'a\nB\nc\n');
    expect(rows).toEqual([
      { kind: 'context', text: 'a' },
      { kind: 'remove', text: 'b' },
      { kind: 'add', text: 'B' },
      { kind: 'context', text: 'c' },
    ]);
  });

  it('keeps a blank line the change adds', () => {
    expect(editToDiffRows('a\n', 'a\n\n')).toEqual([
      { kind: 'context', text: 'a' },
      { kind: 'add', text: '' },
    ]);
  });

  it('an insertion has no removed rows', () => {
    const rows = editToDiffRows('a\n', 'a\nb\n');
    expect(rows.filter((r) => r.kind === 'remove')).toHaveLength(0);
    expect(rows.filter((r) => r.kind === 'add')).toEqual([{ kind: 'add', text: 'b' }]);
  });
});
