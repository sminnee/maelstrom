import { describe, expect, it } from 'vitest';

import { bytes } from './bytes';

describe('bytes', () => {
  it.each([
    ['a few bytes', 512, '512B'],
    ['a shade under a kibibyte', 1023, '1023B'],
    ['exactly a kibibyte', 1024, '1.0K'],
    ['twelve kibibytes', 12 * 1024, '12K'],
    ['a figure that rounds up to ten', 9.96 * 1024 ** 2, '10.0M'],
    ['a gate job', 112_197_632, '107M'],
    // tangier's Python rounds a half to even; toFixed would print 13M and 2.3M.
    ['a half on a whole figure', 12.5 * 1024 ** 2, '12M'],
    ['a half on a tenth', 2.25 * 1024 ** 2, '2.2M'],
    ['several gibibytes', 5.6 * 1024 ** 3, '5.6G'],
  ])('%s reads %s', (_label, size, expected) => {
    expect(bytes(size)).toBe(expected);
  });
});
