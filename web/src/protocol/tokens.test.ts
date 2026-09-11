import { describe, expect, it } from 'vitest';

import { sessionSize } from './tokens';

describe('sessionSize', () => {
  it.each([
    ['nothing spent yet', 0, ''],
    ['a handful', 840, '840 tok'],
    ['a shade under a thousand', 999, '999 tok'],
    ['exactly a thousand', 1_000, '1k tok'],
    ['a working session', 148_000, '148k tok'],
    // Remainders at or above 500 are the only cases that tell truncating from
    // rounding apart. Without them the table passes under either rule.
    ['half a thousand over', 19_500, '19k tok'],
    ['most of a thousand over', 112_900, '112k tok'],
    ['a shade under a million', 999_499, '999k tok'],
    ['high enough to round up to a million, but it does not', 999_500, '999k tok'],
    ['exactly a million', 1_000_000, '1.0M tok'],
    ['a long session', 1_240_000, '1.2M tok'],
    ['most of a tenth over', 1_299_999, '1.2M tok'],
  ])('%s reads %s', (_label, tokens, expected) => {
    expect(sessionSize(tokens)).toBe(expected);
  });

  it('says nothing for a count that is not one', () => {
    expect(sessionSize(-1)).toBe('');
    expect(sessionSize(Number.NaN)).toBe('');
  });
});
