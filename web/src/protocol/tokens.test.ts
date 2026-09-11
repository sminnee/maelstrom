import { describe, expect, it } from 'vitest';

import { contextSize } from './tokens';

describe('contextSize', () => {
  it.each([
    ['nothing read yet', 0, ''],
    ['a handful', 840, '840 ctx'],
    ['a shade under a thousand', 999, '999 ctx'],
    ['exactly a thousand', 1_000, '1k ctx'],
    ['a working context', 148_000, '148k ctx'],
    // Remainders at or above 500 are the only cases that tell truncating from
    // rounding apart. Without them the table passes under either rule.
    ['half a thousand over', 19_500, '19k ctx'],
    ['most of a thousand over', 112_900, '112k ctx'],
    ['a shade under a million', 999_499, '999k ctx'],
    ['high enough to round up to a million, but it does not', 999_500, '999k ctx'],
    ['exactly a million', 1_000_000, '1.0M ctx'],
    ['a context past a million', 1_240_000, '1.2M ctx'],
    ['most of a tenth over', 1_299_999, '1.2M ctx'],
  ])('%s reads %s', (_label, tokens, expected) => {
    expect(contextSize(tokens)).toBe(expected);
  });

  it('says nothing for a count that is not one', () => {
    expect(contextSize(-1)).toBe('');
    expect(contextSize(Number.NaN)).toBe('');
  });
});
