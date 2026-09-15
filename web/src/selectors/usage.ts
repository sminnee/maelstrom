/**
 * What the top bar's chips read: the account's budget, and how many agents run.
 *
 * Pure, and takes `now` rather than reading the clock, so staleness and pace
 * are a table of cases rather than a timing test. `useNow` makes it tick.
 *
 * A chip's colour reads pace, not a percentage: the budget quotient in
 * `CONTEXT.md`. Elapsed time is half of every reading here — the clock for the
 * five-hour window, the working week for the seven-day one — which is why `now`
 * reaches further in than the staleness check.
 */
import type { Agent, Host, HostUsage } from '../protocol/entities';
import type { ChipTone } from '../protocol/chipTone';
import type { WorkCalendar } from '../protocol/workCalendar';
import { WORKING_WEEK, weightedFractionLeft } from '../protocol/workCalendar';
import { ago } from '../protocol/time';
import { isLive } from './graph';

/** Which window a chip reads. The two the account is billed against. */
export type UsageKey = 'fiveHour' | 'sevenDay';

/**
 * How old a reading may be before the chip stops vouching for it.
 *
 * A reading arrives only while an agent takes a turn, so a quiet desk holds
 * the last one indefinitely. Fifteen minutes is long enough to ride out the
 * gaps between turns and short enough that a number left over from this
 * morning is never shown as current.
 */
const STALE_AFTER_MS = 15 * 60_000;

/**
 * The most of a window a reading will admit is left, 0 to 1.
 *
 * Without it a fresh window is ahead of pace on its first turn, because the
 * whole window remains and any spend at all clears the quotient. It binds only
 * while more than 95% of the window is left — a no-op after that — so what it
 * buys is a quiet start until more than 5% of the budget is gone.
 *
 * It bounds the quotient alone. It is a tone-suppression device, not a measure
 * of elapsed time, so a figure a reader sees must not be capped by it.
 */
const MAX_TIME_LEFT = 0.95;

/**
 * Where the quotient changes the tone.
 *
 * One is the pace the window sets, and the comparison is strict: on pace is
 * the window working as intended, so the boundary belongs to the quiet side.
 * 1.5 is an escalation point with no such meaning, so it is inclusive — and
 * loose either way, because a quotient is a ratio of two floats and lands on
 * a round number only by luck. 60% spent with exactly three hours of five
 * left is 1.5 in arithmetic and 1.4999999999999998 here.
 */
const AMBER_AT = 1;
const RED_AT = 1.5;

/**
 * How long each window runs. Nominal — the wire names the reset but never the
 * start, so the length is what makes elapsed time readable. See
 * `docs/dev/agent-daemon.md`.
 */
const LENGTH: Record<UsageKey, number> = {
  fiveHour: 5 * 3_600_000,
  sevenDay: 7 * 86_400_000,
};

const NAMES: Record<UsageKey, string> = {
  fiveHour: '5-hour limit',
  sevenDay: '7-day limit',
};

/**
 * What each window measures its elapsed time against. `null` is wall clock.
 *
 * A table rather than a test on the key, so a window added later has to state
 * its answer. The five-hour window stays on the clock deliberately: it is
 * shorter than the working day it sits inside, so a calendar would soften the
 * reading it exists to give. See the budget quotient in `CONTEXT.md`.
 */
const CALENDAR: Record<UsageKey, WorkCalendar | null> = {
  fiveHour: null,
  sevenDay: WORKING_WEEK,
};

/** What one usage chip shows. */
export interface UsageChip {
  percent: string;
  tone: ChipTone;
  stale: boolean;
  title: string;
}

/** What one window reads, both halves of it. */
export interface BudgetReading {
  /** The budget quotient: time left over budget left. `null` when nothing dates the window. */
  quotient: number | null;
  /**
   * The fraction of the window still to run, weighted where the window uses a
   * calendar. Floored at 0 but never capped: `MAX_TIME_LEFT` bounds the
   * quotient, and this is the figure the title reports.
   */
  timeLeft: number;
}

/**
 * How a window is doing, as both figures rather than only their ratio. The
 * budget quotient in `CONTEXT.md`.
 *
 * `quotient` is `null` when nothing dates the window: with no reset there is
 * no pace to be ahead of, the same way a window with no figure draws no chip.
 */
export function budgetReading(
  key: UsageKey,
  utilization: number,
  resetsAt: number,
  now: number,
): BudgetReading {
  // Negated so an unreadable stamp folds into the same path, as `isStale` does
  // below: `NaN <= 0` is false, and a NaN quotient would read as on pace. A
  // non-finite utilization goes the same way — it reaches the wire intact from
  // Python's `float("nan")`, and would otherwise render a literal `NaN%`.
  if (!(resetsAt > 0) || !Number.isFinite(utilization)) return { quotient: null, timeLeft: 0 };
  const end = resetsAt * 1000;
  // The wire names the reset but never the start, so the nominal length is
  // what makes the window's beginning readable.
  const start = end - LENGTH[key];
  const calendar = CALENDAR[key];
  const fraction = calendar
    ? weightedFractionLeft(start, end, now, calendar)
    : (end - now) / LENGTH[key];
  // Floored outside the branch, so both paths keep the guard. A reset already
  // past leaves no time, never a negative amount: a negative quotient would
  // sort below every threshold and read as the healthiest window on the bar.
  const timeLeft = Math.max(0, fraction);
  const budgetLeft = 1 - utilization;
  // Nothing left to divide by. A spent budget is past any pace, not undefined.
  if (budgetLeft <= 0) return { quotient: Infinity, timeLeft };
  // The cap belongs to the quotient alone. It suppresses the tone at the start
  // of a window, and is not a claim about elapsed time — reporting it as one
  // would tell a reader a fresh window already allows for 5% of the spend.
  return { quotient: Math.min(MAX_TIME_LEFT, timeLeft) / budgetLeft, timeLeft };
}

/**
 * The tone a quotient deserves.
 *
 * On pace is quiet: the budget and the clock running out together is the
 * window working as intended, not news.
 */
export function usageTone(quotient: number | null): ChipTone {
  if (quotient === null) return 'neutral';
  if (quotient >= RED_AT) return 'bad';
  if (quotient > AMBER_AT) return 'busy';
  return 'neutral';
}

/**
 * Whether a chip is worth a band on a bar that has few to spend.
 *
 * Notable means ahead of pace, which is what `usageTone` already reads. A
 * window keeping up says nothing that changes whether to start more work.
 *
 * Stale disqualifies a reading whatever its tone. The narrow bar has room for
 * one thing per band, so it spends its bands on figures it can vouch for; a
 * reading that has aged out gets the wide bar, where `SplitChip` greys it and
 * the title carries its age.
 */
export function isNotable(chip: UsageChip): boolean {
  return !chip.stale && (chip.tone === 'busy' || chip.tone === 'bad');
}

/**
 * One window's chip, or `null` when there is no reading to show.
 *
 * `null` rather than a zero: a bar with no chip is honest about knowing
 * nothing, where `0%` would claim a budget nobody has reported.
 */
export function usageChip(host: Host | undefined, key: UsageKey, now: number): UsageChip | null {
  const usage: HostUsage | null | undefined = host?.usage;
  const window = usage?.[key];
  if (!usage || !window) return null;
  const stale = isStale(usage.at, now);
  const percent = `${Math.round(window.utilization * 100)}%`;
  const { quotient, timeLeft } = budgetReading(key, window.utilization, window.resetsAt, now);
  const tone = usageTone(quotient);
  return {
    percent,
    // What the reading deserves, said plainly. `SplitChip` is what refuses to
    // draw it loud while `stale` holds, so this stays the honest figure.
    tone,
    stale,
    title: titleFor(NAMES[key], percent, window.resetsAt, usage.at, now, stale, timeLeft),
  };
}

/** Whether a reading taken at `at` is too old to stand behind. */
function isStale(at: string, now: number): boolean {
  const age = Date.parse(at);
  // An unreadable stamp is not a fresh one: without a time, nothing vouches
  // for the number.
  if (Number.isNaN(age)) return true;
  return now - age > STALE_AFTER_MS;
}

/**
 * The chip's sentence: what is spent, what the window allows for by now, and
 * how long is left.
 *
 * Both figures are rounded by the same rule, so they are comparable. They are
 * what separates an amber chip from a red one in words, not only in hue.
 *
 * A stale reading says how old it is instead. It cannot vouch for the
 * percentage, so neither a budget figure nor a reset time belongs beside it:
 * both would invite the reader to plan around a figure that has already moved.
 */
function titleFor(
  name: string,
  percent: string,
  resetsAt: number,
  at: string,
  now: number,
  stale: boolean,
  timeLeft: number,
): string {
  const head = `${name}: ${percent} consumed`;
  if (stale) return `${head}, as of ${ago(at, now)} ago`;
  const resets = resetsIn(resetsAt, now);
  // A window the source gave no reset for says nothing about one, the same way
  // a window with no figure draws no chip. Without a window there is also no
  // budget figure to compare the spend against.
  if (!resets) return head;
  const budget = `${Math.round((1 - timeLeft) * 100)}%`;
  return `${head} compared to ${budget} budget. ${resets} remaining`;
}

/**
 * How long until the window rolls over: `6d 1h`, `2h 40m`, `40m`, or `<1m`.
 * Empty when the source named no reset, which reaches here as a zero.
 *
 * Its own formatter rather than `ago`, which rounds to one unit — the hour
 * alone would say "2h" for anything from two hours to three, and the reset is
 * read precisely because the reader is deciding whether to wait for it.
 *
 * It carries a day step because the seven-day window spends most of its life
 * there: in hours alone a fresh week reads "167h 12m", which is the
 * unreadable form this formatter exists to avoid.
 */
function resetsIn(resetsAt: number, now: number): string {
  if (resetsAt <= 0) return '';
  const left = resetsAt * 1000 - now;
  if (left <= 0) return 'now';
  const minutes = Math.floor(left / 60_000);
  if (minutes < 1) return '<1m';
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  // Past a day the minutes are noise: nobody waiting six days needs them.
  if (days) return `${days}d ${hours % 24}h`;
  return hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
}

/** How many agents are alive, and how many of those are mid-turn. */
export interface AgentCounts {
  open: number;
  working: number;
}

/**
 * The counts the `agents` chip shows.
 *
 * Subagents are left to their parent: one `Agent` call would otherwise raise
 * the count twice for work the operator thinks of as one agent.
 */
export function agentCounts(agents: Record<string, Agent>): AgentCounts {
  let open = 0;
  let working = 0;
  for (const agent of Object.values(agents)) {
    if (agent.parent || !isLive(agent)) continue;
    open += 1;
    if (agent.state === 'processing') working += 1;
  }
  return { open, working };
}
