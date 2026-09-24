/**
 * How much working time an interval holds.
 *
 * `time.ts` is about writing a moment on screen; this is about measuring an
 * interval. The measure is weighted: hours nobody works are worth nothing, so
 * a budget read against it does not recover overnight or across a weekend.
 *
 * Pure, and takes its zone by name rather than reading the runner's, so the
 * answer is the same on a laptop and on a UTC CI runner.
 */

/** One recurring stretch of the local week, and what clock time inside it is worth. */
export interface CalendarPeriod {
  /** Local days it applies to, 0 = Sunday to 6 = Saturday, as `Date.getDay()` numbers them. */
  days: readonly number[];
  /** Local hour it opens. Fractional hours allowed: 8.5 is half past eight. */
  fromHour: number;
  /** Local hour it closes, exclusive. */
  toHour: number;
  /** What one millisecond inside it is worth. 1 is full rate, 0.5 half. */
  weight: number;
}

/** Weighted periods read against one named zone. Hours outside every period are worth nothing. */
export interface WorkCalendar {
  periods: readonly CalendarPeriod[];
  /** An IANA zone name. The periods are claims about this zone's wall clock, not about UTC. */
  timeZone: string;
}

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/**
 * The working week a seven-day budget is read against.
 *
 * The working week in `CONTEXT.md`. The zone is fixed rather than read from the
 * browser, so the reading does not depend on where the laptop is.
 */
export const WORKING_WEEK: WorkCalendar = {
  periods: [
    { days: [1, 2, 3, 4, 5], fromHour: 8, toHour: 18, weight: 1 },
    { days: [0, 6], fromHour: 8, toHour: 18, weight: 0.5 },
  ],
  timeZone: 'Pacific/Auckland',
};

/** One instant, as the local wall clock of a named zone reads it. */
interface LocalParts {
  year: number;
  month: number;
  day: number;
  weekday: number;
  hour: number;
  minute: number;
  second: number;
}

/** Sunday-first, to match the numbers `Date.getDay()` uses. */
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatterFor(timeZone: string): Intl.DateTimeFormat {
  let formatter = formatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone,
      weekday: 'short',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      // Midnight reads as 00, not as 24, which would put it on the day before.
      hourCycle: 'h23',
    });
    formatters.set(timeZone, formatter);
  }
  return formatter;
}

/**
 * What `ms` reads as on the named zone's wall clock.
 *
 * Never `Date.getHours()` or `getDay()` — those read the runner's zone, which
 * would make the measure depend on where the machine is.
 */
function localParts(ms: number, timeZone: string): LocalParts {
  const parts = formatterFor(timeZone).formatToParts(new Date(ms));
  const find = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? '';
  return {
    year: Number(find('year')),
    month: Number(find('month')),
    day: Number(find('day')),
    // `-1` rather than a fallback day: an unrecognised weekday matches no
    // period, so the hours read as unweighted. Falling back to 0 would make
    // them Sunday — half rate — and report more headroom than the week has,
    // which is the direction this weighting exists to correct.
    weekday: WEEKDAYS.indexOf(find('weekday')),
    hour: Number(find('hour')),
    minute: Number(find('minute')),
    second: Number(find('second')),
  };
}

/** How far the named zone is ahead of UTC at `ms`, in milliseconds. */
function offsetAt(ms: number, timeZone: string): number {
  const local = localParts(ms, timeZone);
  const asUtc = Date.UTC(
    local.year,
    local.month - 1,
    local.day,
    local.hour,
    local.minute,
    local.second,
  );
  // Whole seconds either side, so the sub-second remainder is not counted twice.
  return asUtc - Math.floor(ms / 1000) * 1000;
}

/**
 * The instant a given local wall-clock time occurs.
 *
 * Going this way needs the offset resolved *at that instant*, which is not
 * known until the instant is. So: guess with the offset at the naive reading,
 * then re-evaluate once with the result as the new guess.
 *
 * The second pass is what lands daylight saving boundaries correctly. It looks
 * redundant and is not — around a transition the first guess falls on the wrong
 * side, and its own offset is what corrects it.
 */
function instantOf(
  parts: { year: number; month: number; day: number },
  hour: number,
  timeZone: string,
): number {
  const naive = Date.UTC(parts.year, parts.month - 1, parts.day) + hour * HOUR;
  let guess = naive - offsetAt(naive, timeZone);
  guess = naive - offsetAt(guess, timeZone);
  return guess;
}

/** The weighted measure of `[from, to)`, in weighted milliseconds. */
export function weightedSpan(from: number, to: number, calendar: WorkCalendar): number {
  // A reversed or empty interval measures nothing. Never a negative: that
  // would flow on as a negative fraction and read as the healthiest window on
  // the bar.
  if (!(to > from)) return 0;

  let total = 0;
  // Start a day early: the local day containing `from` may have begun before
  // it in UTC terms.
  for (let cursor = from - DAY; cursor < to + DAY; cursor += DAY) {
    const day = localParts(cursor, calendar.timeZone);
    for (const period of calendar.periods) {
      if (!period.days.includes(day.weekday)) continue;
      const opens = instantOf(day, period.fromHour, calendar.timeZone);
      const closes = instantOf(day, period.toHour, calendar.timeZone);
      const overlap = Math.min(to, closes) - Math.max(from, opens);
      if (overlap > 0) total += overlap * period.weight;
    }
  }
  return total;
}

/**
 * How much of `[start, end)` is still to come at `now`, 0 to 1, in weighted time.
 *
 * Normalised against the window's own weighted size, so a fresh window reads 1
 * however few working hours it happens to contain.
 */
export function weightedFractionLeft(
  start: number,
  end: number,
  now: number,
  calendar: WorkCalendar,
): number {
  const whole = weightedSpan(start, end, calendar);
  // A window falling entirely outside every period has no working time left.
  // Zero rather than NaN: it flows on as "not ahead of pace", erring quiet,
  // which matches the bias the surrounding code already takes.
  if (whole <= 0) return 0;
  // Clamped, because a reading can arrive before the reconstructed start or
  // after the reset, and neither is a reason to report more than all or less
  // than none.
  const from = Math.min(Math.max(now, start), end);
  return weightedSpan(from, end, calendar) / whole;
}
