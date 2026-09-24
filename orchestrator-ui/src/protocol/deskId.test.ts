import { describe, expect, it } from 'vitest';
import { deskIdForAgent, deskIdForTask, splitDeskId } from './deskId';

describe('desk ids', () => {
  it('carries the task prefix for a task', () => {
    expect(deskIdForTask('a/1')).toBe('task:a/1');
  });

  it('carries the agent prefix for a free agent', () => {
    expect(deskIdForAgent('ag-1')).toBe('agent:ag-1');
  });

  it('splits back into the kind and the id', () => {
    expect(splitDeskId('task:a/1')).toEqual({ kind: 'task', id: 'a/1' });
    expect(splitDeskId('agent:ag-1')).toEqual({ kind: 'agent', id: 'ag-1' });
  });

  it('gives null for an id with no kind', () => {
    expect(splitDeskId('a/1')).toBeNull();
  });

  it('gives null for a kind the desk has no entity for', () => {
    expect(splitDeskId('worktree:a-alpha')).toBeNull();
  });
});
