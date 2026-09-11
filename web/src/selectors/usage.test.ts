import { describe, expect, it } from 'vitest';
import { agentCounts, usageChip, usageTone } from './usage';
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
  it('stays quiet while the budget is not news', () => {
    expect(usageTone(0)).toBe('neutral');
    expect(usageTone(0.74)).toBe('neutral');
  });

  it('turns amber at three quarters, where the budget starts to bite', () => {
    expect(usageTone(0.75)).toBe('busy');
    expect(usageTone(0.89)).toBe('busy');
  });

  it('turns red at nine tenths, where the window is nearly spent', () => {
    expect(usageTone(0.9)).toBe('bad');
    expect(usageTone(1)).toBe('bad');
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
        fiveHour: { utilization: 0.96, resetsAt: 0 },
        sevenDay: null,
        at: '2026-09-11T06:00:00.000Z',
      },
    });
    expect(usageChip(old, 'fiveHour', NOW)?.tone).toBe('bad');
    expect(usageChip(old, 'fiveHour', NOW)?.stale).toBe(true);
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
    expect(usageChip(h, 'fiveHour', NOW)?.title).toBe('5-hour limit: 84% used');
  });

  it('counts a long reset in days, which is where the week window lives', () => {
    const h = host({
      usage: {
        fiveHour: null,
        sevenDay: { utilization: 0.24, resetsAt: Math.floor(NOW / 1000) + 6 * 86_400 + 3600 },
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'sevenDay', NOW)?.title).toBe('7-day limit: 24% used, resets in 6d 1h');
  });

  it('says when the window resets, so the number has somewhere to go', () => {
    const h = host({
      usage: {
        fiveHour: { utilization: 0.84, resetsAt: Math.floor(NOW / 1000) + 9600 },
        sevenDay: null,
        at: '2026-09-11T09:59:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.title).toBe('5-hour limit: 84% used, resets in 2h 40m');
  });

  it('says when a stale reading was taken instead of when it resets', () => {
    const h = host({
      usage: {
        fiveHour: { utilization: 0.1, resetsAt: Math.floor(NOW / 1000) + 600 },
        sevenDay: null,
        at: '2026-09-11T08:00:00.000Z',
      },
    });
    expect(usageChip(h, 'fiveHour', NOW)?.title).toBe('5-hour limit: 10% used, as of 2h ago');
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
    const world = agents({ state: 'idle' }, { state: 'processing' }, { state: 'exited' });
    expect(agentCounts(world).working).toBe(1);
  });

  it('leaves subagents to their parent, so one agent is not counted twice', () => {
    const world = agents({ state: 'processing' }, { state: 'processing', parent: 'ag0' });
    expect(agentCounts(world)).toEqual({ open: 1, working: 1 });
  });

  it('reads an empty world as no agents at all', () => {
    expect(agentCounts({})).toEqual({ open: 0, working: 0 });
  });
});
