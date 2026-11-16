/**
 * The budget schedule: the share of a window's budget its time allows for at
 * each instant. See **Budget schedule** in `CONTEXT.md`. The Budget chart draws it.
 *
 * Pure, and takes `now`, as `usage.ts` does.
 */
import type { UsageWindow } from '../protocol/entities';
import type { WorkingSegment } from '../protocol/workCalendar';
import { segmentsWeight, workingSegments } from '../protocol/workCalendar';
import type { UsageKey } from './usage';
import { budgetReading, duration, windowSpan } from './usage';

/** One point of the curve: an instant in ms, and the share of the budget, 0 to 1. */
export type SchedulePoint = readonly [t: number, fraction: number];

export interface BudgetSchedule {
  /** The window's start and its reset, in ms. */
  start: number;
  end: number;
  /**
   * The schedule, from `[start, 0]` to `[end, 1]`. Straight between points, so
   * the points are the working periods' ends and not samples.
   */
  curve: SchedulePoint[];
  now: number;
  /** The share of the budget the schedule allows for by `now`. */
  budgetedNow: number;
  /** The share of the budget spent, as the reading gives it. */
  spent: number;
  /** The first instant the schedule allows for `spent`. Ahead of pace, it is after `now`. */
  budgetedAt: number;
  /** The budget quotient at `now`, which tones the chip. */
  quotient: number;
}

/**
 * The schedule of one window, or `null` when nothing dates it, or when it holds
 * no working time to schedule a budget over.
 */
export function budgetSchedule(
  key: UsageKey,
  window: UsageWindow,
  now: number,
): BudgetSchedule | null {
  const { quotient, timeLeft } = budgetReading(key, window.utilization, window.resetsAt, now);
  if (quotient === null) return null;
  const { start, end, calendar } = windowSpan(key, window.resetsAt);
  const segments: WorkingSegment[] = calendar
    ? workingSegments(start, end, calendar)
    : [[start, end, 1]];
  const curve = curveOf(start, end, segments);
  if (!curve) return null;
  const spent = window.utilization;
  return {
    start,
    end,
    curve,
    now,
    budgetedNow: 1 - timeLeft,
    spent,
    budgetedAt: instantFor(curve, spent),
    quotient,
  };
}

/**
 * The gap in words: `24% spent, 18% budgeted by now: 6 points ahead. 24% is
 * budgeted for Thu 14:00, in 1d 3h.` Points, not relative percent, so they
 * read off the chart's axis. `clock` writes an instant, in the reader's zone.
 */
export function budgetCaption(s: BudgetSchedule, clock: (ms: number) => string): string {
  const spent = Math.round(s.spent * 100);
  if (s.now >= s.end) return `${spent}% spent in a window that has reset.`;
  const budgeted = Math.round(s.budgetedNow * 100);
  const gap = spent - budgeted;
  const pace = gap > 0 ? `${gap} points ahead` : gap < 0 ? `${-gap} points behind` : 'on pace';
  const when =
    s.budgetedAt >= s.now
      ? `in ${duration(s.budgetedAt - s.now)}`
      : `${duration(s.now - s.budgetedAt)} ago`;
  return (
    `${spent}% spent, ${budgeted}% budgeted by now: ${pace}. ` +
    `${spent}% is budgeted for ${clock(s.budgetedAt)}, ${when}.`
  );
}

/** The cumulative schedule through each segment's ends, or `null` when it holds no weighted time. */
function curveOf(start: number, end: number, segments: WorkingSegment[]): SchedulePoint[] | null {
  const whole = segmentsWeight(segments);
  if (!(whole > 0)) return null;
  const curve: SchedulePoint[] = [[start, 0]];
  let done = 0;
  for (const [opens, closes, weight] of segments) {
    curve.push([opens, done / whole]);
    done += (closes - opens) * weight;
    curve.push([closes, done / whole]);
  }
  // The last close is the whole by construction, so this only carries the
  // flat line through the last night to the reset.
  curve.push([end, 1]);
  return curve;
}

/**
 * The first instant the curve reaches `spent`.
 *
 * The first, because the curve is flat through a night: a spend reached at
 * 6pm is budgeted for 6pm, not for the next morning.
 */
function instantFor(curve: SchedulePoint[], spent: number): number {
  const first = curve[0];
  const last = curve.at(-1);
  if (!first || !last) return 0;
  if (spent <= 0) return first[0];
  if (spent >= 1) return last[0];
  for (let i = 1; i < curve.length; i++) {
    const [t1, f1] = curve[i] ?? last;
    if (f1 < spent) continue;
    const [t0, f0] = curve[i - 1] ?? first;
    return f1 === f0 ? t0 : t0 + ((spent - f0) / (f1 - f0)) * (t1 - t0);
  }
  return last[0];
}
