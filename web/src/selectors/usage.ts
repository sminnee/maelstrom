/**
 * What the top bar's chips read: the account's budget, and how many agents run.
 *
 * Pure, and takes `now` rather than reading the clock, so staleness is a table
 * of cases rather than a timing test. `useNow` is what makes it tick on screen.
 */
import type { Agent, Host, HostUsage } from '../protocol/entities';
import type { ChipTone } from '../protocol/chipTone';
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

/** Where the tone changes. Below the first, a budget is not yet news. */
const WARN_AT = 0.75;
const ALARM_AT = 0.9;

const NAMES: Record<UsageKey, string> = {
  fiveHour: '5-hour limit',
  sevenDay: '7-day limit',
};

/** What one usage chip shows. */
export interface UsageChip {
  percent: string;
  tone: ChipTone;
  stale: boolean;
  title: string;
}

/**
 * The tone a utilisation deserves.
 *
 * The thresholds mirror the point Claude Code itself treats as significant, so
 * the bar and the agent agree about when a budget has started to bite.
 */
export function usageTone(utilization: number): ChipTone {
  if (utilization >= ALARM_AT) return 'bad';
  if (utilization >= WARN_AT) return 'busy';
  return 'neutral';
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
  return {
    percent,
    // What the reading deserves, said plainly. `SplitChip` is what refuses to
    // draw it loud while `stale` holds, so this stays the honest figure.
    tone: usageTone(window.utilization),
    stale,
    title: titleFor(NAMES[key], percent, window.resetsAt, usage.at, now, stale),
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
 * The chip's sentence: what is spent, and what the reader can do about it.
 *
 * A stale reading says how old it is instead of when it resets. It cannot
 * vouch for the percentage, so a reset time beside it would invite the reader
 * to plan around a figure that has already moved.
 */
function titleFor(
  name: string,
  percent: string,
  resetsAt: number,
  at: string,
  now: number,
  stale: boolean,
): string {
  const head = `${name}: ${percent} used`;
  if (stale) return `${head}, as of ${ago(at, now)} ago`;
  const resets = resetsIn(resetsAt, now);
  // A window the source gave no reset for says nothing about one, the same way
  // a window with no figure draws no chip.
  return resets ? `${head}, resets in ${resets}` : head;
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
