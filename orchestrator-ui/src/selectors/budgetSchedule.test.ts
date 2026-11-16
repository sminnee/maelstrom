/**
 * The budget schedule and its inverse. Instants are UTC, with the Auckland wall
 * clock in a comment, as in `workCalendar.test.ts`.
 */
import { describe, expect, it } from 'vitest';
import { budgetCaption, budgetSchedule } from './budgetSchedule';

const HOUR = 3_600_000;
const at = (iso: string) => Date.parse(iso);

// A seven-day window from Thu 2026-09-10 8am to Thu 2026-09-17 8am Auckland:
// five working days and a weekend, sixty weighted hours.
const START = at('2026-09-09T20:00:00.000Z');
const END = at('2026-09-16T20:00:00.000Z');
const RESETS_AT = END / 1000;
const THU_6PM = at('2026-09-10T06:00:00.000Z');

const week = (utilization: number, now: number) =>
  budgetSchedule('sevenDay', { utilization, resetsAt: RESETS_AT }, now);

describe('budgetSchedule', () => {
  it('runs from nothing at the start to the whole budget at the reset, never down', () => {
    const schedule = week(0.3, THU_6PM);
    expect(schedule?.curve[0]).toEqual([START, 0]);
    expect(schedule?.curve.at(-1)).toEqual([END, 1]);
    const curve = schedule?.curve ?? [];
    for (let i = 1; i < curve.length; i++) {
      expect(curve[i]?.[0]).toBeGreaterThanOrEqual(curve[i - 1]?.[0] ?? 0);
      expect(curve[i]?.[1]).toBeGreaterThanOrEqual(curve[i - 1]?.[1] ?? 0);
    }
  });

  it('budgets a spend ahead of pace for a later instant', () => {
    // Thu 6pm: 10 of 60 hours gone. 30% is 18 hours: Fri 4pm.
    const schedule = week(0.3, THU_6PM);
    expect(schedule?.budgetedNow).toBeCloseTo(1 / 6, 12);
    expect(schedule?.budgetedAt).toBeCloseTo(at('2026-09-11T04:00:00.000Z'), -3);
  });

  it('budgets a spend behind pace for an earlier instant', () => {
    // 10% is 6 hours: Thu 2pm.
    expect(week(0.1, THU_6PM)?.budgetedAt).toBeCloseTo(at('2026-09-10T02:00:00.000Z'), -3);
  });

  it('budgets a spend reached at a close for the close, not the next morning', () => {
    expect(week(1 / 6, at('2026-09-11T00:00:00.000Z'))?.budgetedAt).toBe(THU_6PM);
  });

  it('budgets no spend for the start and the whole budget for the reset', () => {
    expect(week(0, THU_6PM)?.budgetedAt).toBe(START);
    expect(week(1, THU_6PM)?.budgetedAt).toBe(END);
  });

  it('allows for the whole budget once the reset has passed', () => {
    const schedule = week(0.3, END + HOUR);
    expect(schedule?.budgetedNow).toBe(1);
    expect(schedule?.budgetedAt).toBeCloseTo(at('2026-09-11T04:00:00.000Z'), -3);
  });

  it('draws the five-hour window on the clock, as one straight line', () => {
    const end = THU_6PM + 5 * HOUR;
    const schedule = budgetSchedule(
      'fiveHour',
      { utilization: 0.2, resetsAt: end / 1000 },
      THU_6PM,
    );
    expect(schedule?.curve).toEqual([
      [THU_6PM, 0],
      [THU_6PM, 0],
      [end, 1],
      [end, 1],
    ]);
    expect(schedule?.budgetedAt).toBe(THU_6PM + HOUR);
  });

  it('has no schedule when nothing dates the window', () => {
    expect(budgetSchedule('sevenDay', { utilization: 0.3, resetsAt: 0 }, THU_6PM)).toBeNull();
  });
});

describe('budgetCaption', () => {
  /** The instant as UTC wall clock, so the case does not depend on the runner's zone. */
  const clock = (ms: number) => new Date(ms).toISOString().slice(5, 16);
  const caption = (spent: number, now = THU_6PM) => {
    const schedule = week(spent, now);
    return schedule && budgetCaption(schedule, clock);
  };

  it('says how far ahead, and when the spend is budgeted for', () => {
    expect(caption(0.3)).toBe(
      '30% spent, 17% budgeted by now: 13 points ahead. 30% is budgeted for 09-11T04:00, in 22h 0m.',
    );
  });

  it('says how far behind, and how long ago the spend was budgeted for', () => {
    expect(caption(0.1)).toBe(
      '10% spent, 17% budgeted by now: 7 points behind. 10% is budgeted for 09-10T02:00, 4h 0m ago.',
    );
  });

  it('says on pace when the rounded figures agree', () => {
    expect(caption(1 / 6)).toMatch(/^17% spent, 17% budgeted by now: on pace\./);
  });

  it('gives no budget figure after the reset', () => {
    expect(caption(0.3, END + HOUR)).toBe('30% spent in a window that has reset.');
  });
});
