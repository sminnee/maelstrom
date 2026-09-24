/**
 * The working week, measured.
 *
 * Every instant is pinned as a UTC ISO string with its Auckland wall clock in a
 * comment. That is the unambiguous way to write these: the assertions are
 * claims about a named zone, and a bare local string would mean something
 * different on a UTC runner than it does here.
 */
import { describe, expect, it } from 'vitest';
import { WORKING_WEEK, weightedFractionLeft, weightedSpan } from './workCalendar';
import type { WorkCalendar } from './workCalendar';

const HOUR = 3_600_000;

/** An instant, written as UTC. */
const at = (iso: string) => Date.parse(iso);

describe('weightedSpan', () => {
  it('reads a full working day as ten weighted hours', () => {
    // Thu 2026-09-10, 8am to 6pm Auckland = 20:00 Wed to 06:00 Thu UTC.
    const from = at('2026-09-09T20:00:00.000Z');
    const to = at('2026-09-10T06:00:00.000Z');
    expect(weightedSpan(from, to, WORKING_WEEK)).toBeCloseTo(10 * HOUR, 0);
  });

  it('reads the overnight gap as nothing, which is the whole point', () => {
    // Thu 6pm to Fri 8am Auckland: fourteen clock hours, none of them working.
    const from = at('2026-09-10T06:00:00.000Z');
    const to = at('2026-09-10T20:00:00.000Z');
    expect(weightedSpan(from, to, WORKING_WEEK)).toBe(0);
  });

  it('reads a weekend day at half rate', () => {
    // Sat 2026-09-12, 8am to 6pm Auckland. Ten clock hours at 0.5.
    const from = at('2026-09-11T20:00:00.000Z');
    const to = at('2026-09-12T06:00:00.000Z');
    expect(weightedSpan(from, to, WORKING_WEEK)).toBeCloseTo(5 * HOUR, 0);
  });

  it('integrates a partial period rather than rounding to whole days', () => {
    // Thu 9:30am to 11:00am Auckland: an hour and a half, starting mid-hour.
    const from = at('2026-09-09T21:30:00.000Z');
    const to = at('2026-09-09T23:00:00.000Z');
    expect(weightedSpan(from, to, WORKING_WEEK)).toBeCloseTo(1.5 * HOUR, 0);
  });

  it('reads an interval wholly inside the night as zero', () => {
    // Thu 1am to 4am Auckland: inside no period at all.
    const from = at('2026-09-10T13:00:00.000Z');
    const to = at('2026-09-10T16:00:00.000Z');
    expect(weightedSpan(from, to, WORKING_WEEK)).toBe(0);
  });

  it('clamps a reversed interval to zero, never to a negative measure', () => {
    // A negative span would flow on as a negative fraction left, which sorts
    // below every threshold and reads as the healthiest window on the bar.
    const from = at('2026-09-10T06:00:00.000Z');
    const to = at('2026-09-09T20:00:00.000Z');
    expect(weightedSpan(from, to, WORKING_WEEK)).toBe(0);
    expect(weightedSpan(from, from, WORKING_WEEK)).toBe(0);
  });

  it('totals sixty weighted hours over a seven-day window', () => {
    // The anchor case: 5 x 10 at full rate, 2 x 10 at half.
    // Thu 2026-09-10 8am to Thu 2026-09-17 8am Auckland.
    const from = at('2026-09-09T20:00:00.000Z');
    const to = at('2026-09-16T20:00:00.000Z');
    expect(weightedSpan(from, to, WORKING_WEEK)).toBeCloseTo(60 * HOUR, 0);
  });

  it('reads the periods against the named zone, not the runner’s', () => {
    // 21:00 UTC Wed is 9am Thursday in Auckland: a working hour there, the
    // small hours anywhere near UTC. Anyone reaching for `getHours()` reads
    // this as outside every period and returns zero.
    const from = at('2026-09-09T21:00:00.000Z');
    const to = at('2026-09-09T22:00:00.000Z');
    expect(weightedSpan(from, to, WORKING_WEEK)).toBeCloseTo(HOUR, 0);
  });

  it('holds the wall clock across a daylight saving change', () => {
    // NZDT begins at 2am local on Sun 2026-09-27 — 2026-09-26T14:00Z — so the
    // offset moves from +12 to +13 in the night between these two weekend
    // days, and that Sunday is 23 hours long.
    //
    // A period is a claim about the wall clock, so both days still run 8am to
    // 6pm and are still worth five weighted hours each. Note the asymmetry
    // that makes this case bite: only 33 hours of UTC elapse across 34 hours
    // of wall clock.
    const from = at('2026-09-25T20:00:00.000Z'); // Sat 8am NZST
    const to = at('2026-09-27T05:00:00.000Z'); // Sun 6pm NZDT
    expect(to - from).toBe(33 * HOUR);
    expect(weightedSpan(from, to, WORKING_WEEK)).toBeCloseTo(10 * HOUR, 0);
  });
});

describe('weightedFractionLeft', () => {
  // A seven-day window: Thu 8am to Thu 8am Auckland.
  const start = at('2026-09-09T20:00:00.000Z');
  const end = at('2026-09-16T20:00:00.000Z');

  it('reads a fresh window as having all its weighted time to come', () => {
    expect(weightedFractionLeft(start, end, start, WORKING_WEEK)).toBeCloseTo(1, 6);
  });

  it('reads a finished window as having none', () => {
    expect(weightedFractionLeft(start, end, end, WORKING_WEEK)).toBe(0);
  });

  it('clamps a `now` outside either end', () => {
    // A reading can arrive before the reconstructed start or after the reset,
    // and neither is a reason to report more than all or less than none.
    expect(weightedFractionLeft(start, end, start - 5 * HOUR, WORKING_WEEK)).toBeCloseTo(1, 6);
    expect(weightedFractionLeft(start, end, end + 5 * HOUR, WORKING_WEEK)).toBe(0);
  });

  it('reads a window with no weighted time as zero rather than NaN', () => {
    // A calendar no part of the window falls inside divides by zero.
    const never: WorkCalendar = { periods: [], timeZone: 'Pacific/Auckland' };
    const fraction = weightedFractionLeft(start, end, start, never);
    expect(fraction).toBe(0);
    expect(Number.isNaN(fraction)).toBe(false);
  });

  it('reads Friday evening and Monday morning alike, which is the flap this removes', () => {
    // The symptom the feature exists to remove. Between Friday 6pm and Monday
    // 8am nobody could work, so a budget untouched across the weekend must not
    // look healthier on Monday than it did on Friday. On the clock these two
    // instants are 2.6 days apart and the reading visibly recovers.
    const fridayEvening = at('2026-09-11T06:00:00.000Z'); // Fri 6pm
    const mondayMorning = at('2026-09-13T20:00:00.000Z'); // Mon 8am
    const friday = weightedFractionLeft(start, end, fridayEvening, WORKING_WEEK);
    const monday = weightedFractionLeft(start, end, mondayMorning, WORKING_WEEK);
    // Only the weekend's ten weighted hours separate them, against sixty in
    // the window: the clock alone would put 2.6 of seven days between them.
    expect(monday).toBeCloseTo(friday - 10 / 60, 6);
  });

  it('spends a weekend day at half the rate of the working day after it', () => {
    // Read on the clock these are both one day. Weighted, the Sunday is worth
    // half the Monday, which is what separates the table's two entries: a
    // comparison of Saturday against Sunday would hold even if the weekday
    // rate were also 0.5, and so would pin nothing.
    const saturdayEvening = at('2026-09-12T06:00:00.000Z'); // Sat 6pm
    const sundayEvening = at('2026-09-13T06:00:00.000Z'); // Sun 6pm
    const mondayEvening = at('2026-09-14T06:00:00.000Z'); // Mon 6pm
    const overSunday =
      weightedFractionLeft(start, end, saturdayEvening, WORKING_WEEK) -
      weightedFractionLeft(start, end, sundayEvening, WORKING_WEEK);
    const overMonday =
      weightedFractionLeft(start, end, sundayEvening, WORKING_WEEK) -
      weightedFractionLeft(start, end, mondayEvening, WORKING_WEEK);
    expect(overSunday).toBeCloseTo(5 / 60, 6);
    expect(overMonday).toBeCloseTo(10 / 60, 6);
  });
});
