import { describe, expect, it } from 'vitest';
import { canConnect, followsAfterConnect, followsAfterDisconnect } from './connect';

/** The two ends of a drag, as React Flow reports them. */
const pair = (source: string, target: string) => ({ source, target });

describe('canConnect', () => {
  it('allows two tasks in one project', () => {
    expect(canConnect(pair('northwind/NORT-7', 'northwind/NORT-9'))).toBe(true);
  });

  it('refuses a task following itself', () => {
    expect(canConnect(pair('northwind/NORT-7', 'northwind/NORT-7'))).toBe(false);
  });

  // The server refuses it too; declining here stops the wire ever landing.
  it('refuses a pair in different projects', () => {
    expect(canConnect(pair('maelstrom/MAEL-40', 'northwind/NORT-9'))).toBe(false);
  });

  it('refuses a half-made connection', () => {
    expect(canConnect({ source: null, target: 'northwind/NORT-9' })).toBe(false);
    expect(canConnect({ source: 'northwind/NORT-7', target: null })).toBe(false);
  });

  // A free agent is on the board but is no task, so it has no follows to write.
  it('refuses an id that is not a qualified task', () => {
    expect(canConnect(pair('f2c6a9d4', 'northwind/NORT-9'))).toBe(false);
  });
});

describe('followsAfterConnect', () => {
  it('adds the followed id to what the target already follows', () => {
    expect(followsAfterConnect(['northwind/NORT-1'], 'northwind/NORT-7')).toEqual([
      'northwind/NORT-1',
      'northwind/NORT-7',
    ]);
  });

  it('writes the first wire onto a task that follows nothing', () => {
    expect(followsAfterConnect([], 'northwind/NORT-7')).toEqual(['northwind/NORT-7']);
  });

  // Dragging the same wire twice must not write the id twice.
  it('leaves a wire that is already there alone', () => {
    expect(followsAfterConnect(['northwind/NORT-7'], 'northwind/NORT-7')).toEqual([
      'northwind/NORT-7',
    ]);
  });
});

describe('followsAfterDisconnect', () => {
  it('drops the followed id and keeps the rest', () => {
    expect(
      followsAfterDisconnect(['northwind/NORT-1', 'northwind/NORT-7'], 'northwind/NORT-7'),
    ).toEqual(['northwind/NORT-1']);
  });

  it('clears the last wire to an empty list, which is a real edit', () => {
    expect(followsAfterDisconnect(['northwind/NORT-7'], 'northwind/NORT-7')).toEqual([]);
  });

  it('leaves a list that never held the id alone', () => {
    expect(followsAfterDisconnect(['northwind/NORT-1'], 'northwind/NORT-7')).toEqual([
      'northwind/NORT-1',
    ]);
  });
});
