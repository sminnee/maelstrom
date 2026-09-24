import { describe, expect, it } from 'vitest';
import { agentCounts, budgetReading, isNotable, usageChip, usageTone } from './usage';
import type { UsageChip } from './usage';
import type { Agent, Host } from '../protocol/entities';
import { makeAgent } from '../test/fixtures';

const NOW = Date.parse('2026-09-11T10:00:00.000Z');

function host(over: Partial<Host> = {}): Host {
  return {
    id: 'agent-host',
    reachable: true,
    since: '2026-09-11T09:00:00.000Z',
    socket: '/x.sock',
    usage: null,
    ...over,
  };
}

describe('usageTone', () => {
  it('stays quiet while the budget outlasts the clock', () => {
    expect(usageTone(0.5)).toBe('neutral');
    expect(usageTone(0.99)).toBe('neutral');
  });

  it('reads exactly on pace as quiet, not as news', () => {
    // The budget and the clock run out together. Nothing has gone wrong yet,
    // so the boundary belongs to the quiet side.
    expect(usageTone(1)).toBe('neutral');
  });

  it('turns amber once the budget drains faster than the clock', () => {
    expect(usageTone(1.01)).toBe('busy');
    expect(usageTone(1.49)).toBe('busy');
  });

  it('turns red at half again the pace, where the window will not last', () => {
    expect(usageTone(1.5)).toBe('bad');
    expect(usageTone(Infinity)).toBe('bad');
  });

  it('stays quiet when nothing dates the window', () => {
    // With no reset there is no pace to be ahead of. There is no absolute
    // rule left to fall back on, so the honest answer is to say nothing.
    expect(usageTone(null)).toBe('neutral');
  });
});

describe('budgetReading', () => {
  /** A reset `ms` from `NOW`, in the unix seconds the wire carries. */
  const resetIn = (ms: number) => Math.floor((NOW + ms) / 1000);

  it('reads one when the budget and the clock run out together', () => {
    // Two hours into a five-hour window with 40% spent: three hours and 60%
    // both remain, so neither is ahead. The tone is asserted beside the
    // number because `toBeCloseTo` spans both sides of the threshold, and
    // which side one falls on is the decision under test.
    const { quotient: q } = budgetReading('fiveHour', 0.4, resetIn(3 * 3_600_000), NOW);
    expect(q).toBeCloseTo(1);
    expect(usageTone(q)).toBe('neutral');
  });

  it('rises above one when the budget is the first to run out', () => {
    expect(budgetReading('fiveHour', 0.5, resetIn(3 * 3_600_000), NOW).quotient).toBeCloseTo(1.2);
  });

  it('measures the week against seven days, not five hours', () => {
    // One day into the week, 30% spent. Read against the five-hour length the
    // time left would overflow the cap and the reading would be nonsense.
    //
    // The figure is the weighted one: `NOW` is Friday 10pm in Auckland, and
    // the day that has passed was a working Thursday, so it cost ten of the
    // week's sixty weighted hours rather than a flat seventh. On the clock
    // this read 1.224.
    expect(budgetReading('sevenDay', 0.3, resetIn(6 * 86_400_000), NOW).quotient).toBeCloseTo(
      1.19,
      2,
    );
  });

  it('spends the week in working time, so an overnight gain does not flatter it', () => {
    // The flap this feature removes, read at the seam the chip uses. Friday
    // 10pm and the Monday 8am after it are 2.4 days apart on the clock, which
    // alone would hand the reading back a third of the window. Weighted, only
    // the weekend's ten hours separate them, so the quotient falls rather than
    // recovering.
    const end = resetIn(6 * 86_400_000);
    const mondayMorning = Date.parse('2026-09-13T20:00:00.000Z');
    const friday = budgetReading('sevenDay', 0.3, end, NOW).quotient;
    const monday = budgetReading('sevenDay', 0.3, end, mondayMorning).quotient;
    expect(monday).toBeLessThan(friday!);
  });

  it('leaves the five-hour window on the clock, so a late session is not softened', () => {
    // 4pm to 9pm in Auckland is mostly outside the working day, and weighting
    // would call three of its five hours free. The same spend at the same
    // point of the window must read alike whenever it runs: this is the
    // decision that the short window answers "am I about to hit the wall".
    const afternoon = Date.parse('2026-09-11T04:00:00.000Z'); // Fri 4pm
    const lateEvening = Date.parse('2026-09-11T09:00:00.000Z'); // Fri 9pm
    const twoHoursIn = (now: number) => Math.floor((now + 3 * 3_600_000) / 1000);
    expect(budgetReading('fiveHour', 0.5, twoHoursIn(afternoon), afternoon).quotient).toBeCloseTo(
      budgetReading('fiveHour', 0.5, twoHoursIn(lateEvening), lateEvening).quotient!,
      10,
    );
  });

  it('caps the time left, so a fresh window cannot be ahead of pace on a trickle', () => {
    // At the very start the whole window remains, and any spend at all would
    // clear one. Uncapped this reading is 1.0417.
    expect(budgetReading('fiveHour', 0.04, resetIn(5 * 3_600_000), NOW).quotient).toBeCloseTo(
      0.9896,
      4,
    );
  });

  it('puts the cap exactly on the pace line at 5% spent', () => {
    // The bound the constant is chosen for: 0.95/0.95. An inequality would
    // hold for any cap at or below 0.95, so the value is asserted instead.
    const { quotient: q } = budgetReading('fiveHour', 0.05, resetIn(5 * 3_600_000), NOW);
    expect(q).toBeCloseTo(1, 10);
    expect(usageTone(q)).toBe('neutral');
  });

  it('stops binding once the window is past its first twentieth', () => {
    // The cap is a start-of-window guard, not a general one. With less than
    // 95% of the window left it is a no-op, and pace alone decides.
    expect(budgetReading('fiveHour', 0.049, resetIn(4.5 * 3_600_000), NOW).quotient).toBeCloseTo(
      0.9464,
      4,
    );
  });

  it('reads a spent budget as past any pace', () => {
    // Nothing remains to divide by. The window cannot outlast a budget that
    // is already gone.
    expect(budgetReading('fiveHour', 1, resetIn(3_600_000), NOW).quotient).toBe(Infinity);
  });

  it('reads a reset already past as no time left, never as negative', () => {
    // A negative quotient would sort below every threshold and read as
    // healthy, which is the opposite of what an overdue window means.
    expect(budgetReading('fiveHour', 0.5, resetIn(-600_000), NOW).quotient).toBe(0);
  });

  it('reads nothing from a window the source gave no reset for', () => {
    expect(budgetReading('fiveHour', 0.84, 0, NOW).quotient).toBeNull();
  });

  it('reads nothing from a spend the source could not put a number on', () => {
    // `float("nan")` parses on the Python side, so a NaN crosses the wire
    // intact. Left ungated it divides through to a NaN quotient, which reads
    // as quiet because every comparison against NaN is false — and the chip
    // renders the spend as a literal `NaN%`.
    expect(budgetReading('fiveHour', NaN, resetIn(3 * 3_600_000), NOW).quotient).toBeNull();
    expect(budgetReading('fiveHour', Infinity, resetIn(3 * 3_600_000), NOW).quotient).toBeNull();
  });
});

describe('usageChip', () => {
  it('reads nothing from a host that has heard nothing', () => {
    expect(usageChip(host(), 'fiveHour', NOW)).toBeNull();
    expect(usageChip(undefined, 'fiveHour', NOW)).toBeNull();
  });

  it('reads the window it is asked for', () => {
    const h = host({
      usage: {
        fiveHour: { utilization: 0.07, resetsAt: 1788241800 },
        sevenDay: { utilization: 0.24, resetsAt: 1788480000 },
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.percent).toBe('7%');
    expect(usageChip(h, 'sevenDay', NOW)?.percent).toBe('24%');
  });

  it('rounds to whole percent, which is all the source reports', () => {
    const h = host({
      usage: {
        // A value no rounding rule agrees on, so the assertion discriminates:
        // floor gives 7%, ceil gives 8%.
        fiveHour: { utilization: 0.076, resetsAt: 0 },
        sevenDay: null,
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.percent).toBe('8%');
  });

  it('reads a window the host has no figure for as nothing', () => {
    const h = host({
      usage: { fiveHour: null, sevenDay: null, at: '2026-09-11T09:59:00.000Z' },
    });
    expect(usageChip(h, 'fiveHour', NOW)).toBeNull();
  });

  it('marks a reading older than the window stale', () => {
    const fresh = host({
      usage: {
        fiveHour: { utilization: 0.8, resetsAt: 0 },
        sevenDay: null,
        at: '2026-09-11T09:50:00.000Z',
      },
    });
    const old = host({
      usage: {
        fiveHour: { utilization: 0.8, resetsAt: 0 },
        sevenDay: null,
        at: '2026-09-11T08:00:00.000Z',
      },
    });
    expect(usageChip(fresh, 'fiveHour', NOW)?.stale).toBe(false);
    expect(usageChip(old, 'fiveHour', NOW)?.stale).toBe(true);
  });

  it('reports the honest tone even when stale, and says it is stale', () => {
    // The selector says what the reading deserves; `SplitChip` is what
    // refuses to draw it loud. Splitting it that way means one component
    // enforces the rule rather than every caller remembering it.
    const old = host({
      usage: {
        // Two thirds spent with most of the window left: well past pace, so
        // the tone is earned rather than assumed.
        fiveHour: { utilization: 0.66, resetsAt: Math.floor(NOW / 1000) + 4 * 3600 },
        sevenDay: null,
        at: '2026-09-11T06:00:00.000Z',
      },
    });
    expect(usageChip(old, 'fiveHour', NOW)?.tone).toBe('bad');
    expect(usageChip(old, 'fiveHour', NOW)?.stale).toBe(true);
  });

  it('colours a window spending faster than its clock', () => {
    // The worked example: two hours into five, 41% spent where 40% is the
    // pace. A fixed threshold read this as quiet all the way to 75%.
    const h = host({
      usage: {
        fiveHour: { utilization: 0.41, resetsAt: Math.floor(NOW / 1000) + 3 * 3600 },
        sevenDay: null,
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.tone).toBe('busy');
  });

  it('leaves a nearly spent window quiet when it is about to reset', () => {
    // 84% spent with six minutes to go. The old rule called this alarming;
    // there is not enough window left to spend what remains.
    const h = host({
      usage: {
        fiveHour: { utilization: 0.84, resetsAt: Math.floor(NOW / 1000) + 360 },
        sevenDay: null,
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.tone).toBe('neutral');
  });

  it('reads the week window against its own length', () => {
    // One day in. 30% is ahead of the 14% pace; 10% is behind it.
    const week = (utilization: number) =>
      host({
        usage: {
          fiveHour: null,
          sevenDay: { utilization, resetsAt: Math.floor(NOW / 1000) + 6 * 86_400 },
          at: '2026-09-11T09:59:00.000Z',
        },
      });
    expect(usageChip(week(0.3), 'sevenDay', NOW)?.tone).toBe('busy');
    expect(usageChip(week(0.1), 'sevenDay', NOW)?.tone).toBe('neutral');
  });

  it('stays quiet on a window the source gave no reset for', () => {
    // No reset, no pace. The figure still shows; only the colour withholds.
    const h = host({
      usage: {
        fiveHour: { utilization: 0.84, resetsAt: 0 },
        sevenDay: null,
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.tone).toBe('neutral');
    expect(usageChip(h, 'fiveHour', NOW)?.percent).toBe('84%');
  });

  it('says nothing about a reset the source never reported', () => {
    // The server defaults a missing resetsAt to 0, so it arrives as a real
    // value. "resets in now" would be a claim about a rollover nobody named.
    const h = host({
      usage: {
        fiveHour: { utilization: 0.84, resetsAt: 0 },
        sevenDay: null,
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.title).toBe('5-hour limit: 84% consumed');
  });

  it('counts a long reset in days, which is where the week window lives', () => {
    const h = host({
      usage: {
        fiveHour: null,
        sevenDay: { utilization: 0.24, resetsAt: Math.floor(NOW / 1000) + 6 * 86_400 + 3600 },
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    // The whole sentence, as a reader sees it. 17% is the budget the window
    // allows for by now: one working Thursday out of sixty weighted hours.
    expect(usageChip(h, 'sevenDay', NOW)?.title).toBe(
      '7-day limit: 24% consumed compared to 17% budget. 6d 1h remaining',
    );
  });

  it('says when the window resets, so the number has somewhere to go', () => {
    const h = host({
      usage: {
        fiveHour: { utilization: 0.84, resetsAt: Math.floor(NOW / 1000) + 9600 },
        sevenDay: null,
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    // 47% is the budget: 2h 40m of the five hours remain, so 53% is left and
    // the window allows for the rest. A spend of 84% against it is the gap the
    // colour also reports.
    expect(usageChip(h, 'fiveHour', NOW)?.title).toBe(
      '5-hour limit: 84% consumed compared to 47% budget. 2h 40m remaining',
    );
  });

  it('shows a fresh window as no budget spent, not as the cap', () => {
    // `MAX_TIME_LEFT` suppresses the tone at the start of a window; it is not a
    // claim about elapsed time. Rendering the clamped figure told the reader a
    // window with nothing elapsed already allowed for 5% of the spend — beside
    // a spend figure they are invited to compare it against.
    const h = host({
      usage: {
        fiveHour: { utilization: 0, resetsAt: Math.floor(NOW / 1000) + 5 * 3600 },
        sevenDay: null,
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.title).toBe(
      '5-hour limit: 0% consumed compared to 0% budget. 5h 0m remaining',
    );
  });

  it('keeps the two coloured tones apart in words, not by hue alone', () => {
    // A screen reader gets the title and nothing else, so amber and red must
    // not share a sentence. The retired clause did that with different words;
    // the figures do it with different numbers. Either way the titles differ,
    // which is the property worth holding — a budget figure that went coarse,
    // or dropped out, would collapse these two to identical prose.
    const at = (utilization: number, hoursLeft: number) =>
      host({
        usage: {
          fiveHour: { utilization, resetsAt: Math.floor(NOW / 1000) + hoursLeft * 3600 },
          sevenDay: null,
          at: '2026-09-11T09:59:00.000Z',
        },
      });
    const amber = usageChip(at(0.45, 3), 'fiveHour', NOW);
    const red = usageChip(at(0.7, 3), 'fiveHour', NOW);
    expect(amber?.tone).toBe('busy');
    expect(red?.tone).toBe('bad');
    expect(amber?.title).not.toBe(red?.title);
  });

  it('says the same thing on a window that is keeping up', () => {
    // The figures replace the pace clause on every tone, so a quiet chip
    // carries the same sentence as a coloured one. The gap between the two
    // numbers is what the reader compares, and it reads without the colour —
    // which is what a screen reader gets.
    const h = host({
      usage: {
        fiveHour: { utilization: 0.2, resetsAt: Math.floor(NOW / 1000) + 9600 },
        sevenDay: null,
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.tone).toBe('neutral');
    expect(usageChip(h, 'fiveHour', NOW)?.title).toBe(
      '5-hour limit: 20% consumed compared to 47% budget. 2h 40m remaining',
    );
  });

  it('says when a stale reading was taken instead of when it resets', () => {
    const h = host({
      usage: {
        fiveHour: { utilization: 0.1, resetsAt: Math.floor(NOW / 1000) + 600 },
        sevenDay: null,
        at: '2026-09-11T08:00:00.000Z',
      },
    });
    // No budget figure beside it: a stale reading cannot vouch for the spend,
    // so a comparison would invite planning around a number that has moved.
    expect(usageChip(h, 'fiveHour', NOW)?.title).toBe('5-hour limit: 10% consumed, as of 2h ago');
  });
});

describe('isNotable', () => {
  const chip = (over: Partial<UsageChip> = {}): UsageChip => ({
    percent: '50%',
    tone: 'neutral',
    stale: false,
    title: '',
    ...over,
  });

  it('keeps a window that is ahead of pace', () => {
    expect(isNotable(chip({ tone: 'busy' }))).toBe(true);
    expect(isNotable(chip({ tone: 'bad' }))).toBe(true);
  });

  it('drops a window that is keeping up, which says nothing to act on', () => {
    expect(isNotable(chip({ tone: 'neutral' }))).toBe(false);
  });

  it('drops a stale reading whatever its tone', () => {
    // The clause that costs the narrow bar its alarm on a quiet desk: a
    // reading arrives only while an agent takes a turn, so `bad` and `stale`
    // together is the resting state rather than an edge case. The narrow bar
    // trades that alarm for a quiet row; the wide bar still greys and shows it.
    expect(isNotable(chip({ tone: 'bad', stale: true }))).toBe(false);
    expect(isNotable(chip({ tone: 'busy', stale: true }))).toBe(false);
  });

  it('drops the tones no usage window earns today', () => {
    // `usageTone` returns only neutral, busy and bad. The rule names the two
    // it keeps rather than the ones it drops, so a tone added later is quiet
    // until someone decides it is worth a band.
    expect(isNotable(chip({ tone: 'good' }))).toBe(false);
    expect(isNotable(chip({ tone: 'quiet' }))).toBe(false);
    expect(isNotable(chip({ tone: 'special' }))).toBe(false);
  });
});

describe('agentCounts', () => {
  const agents = (...list: Partial<Agent>[]): Record<string, Agent> =>
    Object.fromEntries(
      list.map((a, i) => {
        const agent = makeAgent({ id: `ag${i}`, ...a });
        return [agent.id, agent];
      }),
    );

  it('counts every agent that has not exited as open', () => {
    const world = agents(
      { state: 'idle' },
      { state: 'processing' },
      { state: 'awaiting-question' },
      { state: 'exited' },
    );
    expect(agentCounts(world).open).toBe(3);
  });

  it('counts only the ones taking a turn as working', () => {
    const world = agents(
      { state: 'idle' },
      { state: 'processing' },
      { state: 'delegating' },
      { state: 'exited' },
    );
    expect(agentCounts(world).working).toBe(2);
  });

  it('leaves subagents to their parent, so one agent is not counted twice', () => {
    const world = agents({ state: 'processing' }, { state: 'processing', parent: 'ag0' });
    expect(agentCounts(world)).toEqual({ open: 1, working: 1 });
  });

  it('reads an empty world as no agents at all', () => {
    expect(agentCounts({})).toEqual({ open: 0, working: 0 });
  });
});
