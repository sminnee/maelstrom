/**
 * How a moment is written on screen: as an age, or as a time on the clock.
 *
 * Both take `now` rather than reading the clock themselves, so a caller
 * decides when "now" is and a test is a table of pure cases. The one shared
 * clock that makes an age tick is `useNow`.
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;

/** `ts` as milliseconds, or `null` when there is nothing there to read. */
function parse(ts: string): number | null {
  if (!ts) return null;
  const ms = Date.parse(ts);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * The age of a moment, as the one short unit a status line holds:
 * `<1m`, `7m`, `2h`, `3d`.
 *
 * Rounds down, so "2h" means at least two hours. An empty or unreadable `ts`
 * gives an empty string: the caller shows nothing rather than a fake age. An
 * unstamped event is a real state, not an error.
 */
export function ago(ts: string, now: number): string {
  const at = parse(ts);
  if (at === null) return '';
  const elapsed = now - at;
  if (elapsed < MINUTE) return '<1m';
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)}m`;
  if (elapsed < DAY) return `${Math.floor(elapsed / HOUR)}h`;
  return `${Math.floor(elapsed / DAY)}d`;
}

/**
 * How long since `ts`, in milliseconds, or `null` when there is no readable
 * stamp to measure from.
 *
 * Separate from {@link ago} because a caller that compares against a threshold
 * needs the number, not the label — and must not re-parse the stamp itself,
 * where an unreadable one would silently become `NaN`.
 */
export function silentFor(ts: string, now: number): number | null {
  const at = parse(ts);
  return at === null ? null : now - at;
}

/**
 * The time of a moment, in the reader's own locale: `7:31 pm` today,
 * `Tue 7:31 pm` earlier this week, `4 Sep` older.
 *
 * Never a year — a transcript older than a year is not one anyone reads. An
 * empty or unreadable `ts` gives an empty string, as `ago` does.
 */
export function clockTime(ts: string, now: number): string {
  const at = parse(ts);
  if (at === null) return '';
  const then = new Date(at);
  const elapsed = now - at;
  if (elapsed >= WEEK) {
    return format(then, { day: 'numeric', month: 'short' });
  }
  const sameDay = then.toDateString() === new Date(now).toDateString();
  const time = format(then, { hour: 'numeric', minute: '2-digit' });
  return sameDay ? time : `${format(then, { weekday: 'short' })} ${time}`;
}

/** One formatted part, with the meridiem lower-cased: `7:31 pm`, not `7:31 PM`. */
function format(at: Date, options: Intl.DateTimeFormatOptions): string {
  return (
    new Intl.DateTimeFormat(undefined, options)
      .format(at)
      .replace(/\b(AM|PM)\b/g, (m) => m.toLowerCase())
      // Intl separates the time from the meridiem with a narrow no-break space.
      // A plain space is what a reader — and a test — expects to see.
      .replace(/[\u202f\u00a0]/g, ' ')
  );
}
