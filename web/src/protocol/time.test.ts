import { describe, expect, it } from 'vitest';

import { ago, clockTime, silentFor } from './time';

const NOW = Date.parse('2026-09-05T19:31:00Z');

/** `NOW` minus `ms`, as the ISO string an event carries. */
function before(ms: number): string {
  return new Date(NOW - ms).toISOString();
}

describe('ago', () => {
  it.each([
    ['under a minute', 0, '<1m'],
    ['a second short of a minute', 59_000, '<1m'],
    ['exactly a minute', 60_000, '1m'],
    ['a second short of an hour', 59 * 60_000 + 59_000, '59m'],
    ['exactly an hour', 60 * 60_000, '1h'],
    ['a second short of a day', 23 * 3_600_000 + 3_599_000, '23h'],
    ['exactly a day', 24 * 3_600_000, '1d'],
    ['three days and a bit', 3 * 86_400_000 + 3_600_000, '3d'],
  ])('%s reads %s', (_label, elapsed, expected) => {
    expect(ago(before(elapsed), NOW)).toBe(expected);
  });

  it('rounds down, so "2h" means at least two hours', () => {
    expect(ago(before(2 * 3_600_000 + 59 * 60_000), NOW)).toBe('2h');
  });

  it('says nothing when there is no time to report', () => {
    expect(ago('', NOW)).toBe('');
    expect(ago('not a time', NOW)).toBe('');
  });

  it('says nothing about a time in the future', () => {
    expect(ago(new Date(NOW + 60_000).toISOString(), NOW)).toBe('<1m');
  });
});

describe('silentFor', () => {
  it('gives the elapsed milliseconds', () => {
    expect(silentFor(before(90_000), NOW)).toBe(90_000);
  });

  it('gives null when there is no stamp to measure from', () => {
    // The caller must not get NaN, which compares false against every
    // threshold and so hides an unstamped agent behind a passing guard.
    expect(silentFor('', NOW)).toBeNull();
    expect(silentFor('not a time', NOW)).toBeNull();
  });
});

describe('clockTime', () => {
  it('says nothing when there is no time to report', () => {
    expect(clockTime('', NOW)).toBe('');
    expect(clockTime('not a time', NOW)).toBe('');
  });

  it('gives the time alone for a moment today', () => {
    const today = clockTime(before(2 * 3_600_000), NOW);
    expect(today).toMatch(/^\d{1,2}:\d{2} (am|pm)$/);
  });

  it('names the weekday for a moment earlier this week', () => {
    const earlier = clockTime(before(3 * 86_400_000), NOW);
    expect(earlier).toMatch(/^[A-Z][a-z]{2} \d{1,2}:\d{2} (am|pm)$/);
  });

  it('gives day and month, never a year, for anything older', () => {
    const old = clockTime(before(30 * 86_400_000), NOW);
    expect(old).toMatch(/^\d{1,2} [A-Z][a-z]{2}$/);
    expect(old).not.toMatch(/2026/);
  });
});
