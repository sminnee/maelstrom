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

  // These assert which parts a moment is written with, not how the reader's
  // locale arranges them: `en-US` gives "Aug 6" where `en-NZ` gives "6 Aug",
  // and `en-GB` uses a 24-hour clock with no meridiem at all. Pinning one
  // arrangement pins the machine the suite happens to run on.

  it('gives the time alone for a moment today', () => {
    const today = clockTime(before(2 * 3_600_000), NOW);
    expect(today).toMatch(/\d{1,2}:\d{2}/);
    expect(today).not.toMatch(/[A-Za-z]{3}/);
  });

  it('names the weekday for a moment earlier this week', () => {
    const earlier = clockTime(before(3 * 86_400_000), NOW);
    expect(earlier).toMatch(/\d{1,2}:\d{2}/);
    // The weekday of `NOW` minus three days, in whatever the locale calls it.
    const weekday = new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(
      new Date(NOW - 3 * 86_400_000),
    );
    expect(earlier).toContain(weekday);
  });

  it('gives day and month, never a year or a time, for anything older', () => {
    const at = new Date(NOW - 30 * 86_400_000);
    const old = clockTime(at.toISOString(), NOW);
    // The runner's own timezone decides the calendar day, so read both parts
    // from it rather than naming them.
    const part = (options: Intl.DateTimeFormatOptions) =>
      new Intl.DateTimeFormat(undefined, options).format(at);
    expect(old).toContain(part({ day: 'numeric' }));
    expect(old).toContain(part({ month: 'short' }));
    expect(old).not.toMatch(/2026/);
    expect(old).not.toMatch(/\d{1,2}:\d{2}/);
  });

  it('lower-cases the meridiem where the locale has one', () => {
    const today = clockTime(before(2 * 3_600_000), NOW);
    expect(today).not.toMatch(/\b(AM|PM)\b/);
  });
});
