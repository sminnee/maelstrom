import { describe, expect, it } from 'vitest';
import { ownerOf } from './ids';

describe('ownerOf', () => {
  it.each([
    ['laptop:northwind-alpha', 'laptop'],
    ['northwind-alpha', ''],
    // A `:` of the id's own, after a prefix that is no host name.
    ['2026-01-01T10:00', ''],
  ])('reads the host of %s', (id, host) => {
    expect(ownerOf(id)).toBe(host);
  });
});
