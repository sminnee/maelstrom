import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { keys } from '../api/keys';
import { FakeEventSource } from '../fake/fakeEventSource';
import type { ChangeNotice } from '../api/types';
import type { ConnectionState } from './changeStream';
import { invalidationsFor, startChangeStream } from './changeStream';

describe('invalidationsFor', () => {
  it.each<[ChangeNotice, unknown[]]>([
    [{ kind: 'project', ids: ['p'] }, [keys.projects()]],
    [{ kind: 'worktree', ids: ['w'] }, [keys.worktrees(), keys.worktreeChanges.of('w')]],
    [{ kind: 'worktree', ids: [] }, [keys.worktrees(), keys.worktreeChanges.all()]],
    [{ kind: 'desk', ids: ['task:a'] }, [keys.desk()]],
    [{ kind: 'host', ids: ['agent-host'] }, [keys.host()]],
    [{ kind: 'attention', ids: ['at1'] }, [keys.attention()]],
    [
      { kind: 'task', ids: ['p/1', 'p/2'] },
      [keys.tasks.list(), keys.tasks.detail('p/1'), keys.tasks.detail('p/2')],
    ],
    [{ kind: 'task', ids: [] }, [keys.tasks.all()]],
    [{ kind: 'agent', ids: ['ag1'] }, [keys.agents.list(), keys.agents.detail('ag1')]],
    [{ kind: 'document', ids: ['d1'] }, [keys.documents.list(), keys.documents.detail('d1')]],
    [
      { kind: 'comm', ids: ['c1', 'c2'] },
      [
        ['comms', 'list'],
        ['comms', 'detail', 'c1'],
        ['comms', 'detail', 'c2'],
      ],
    ],
    [{ kind: 'comm', ids: [] }, [['comms']]],
    [
      { kind: 'operation', ids: ['op1'] },
      [
        ['operations', 'list'],
        ['operations', 'log', 'op1'],
      ],
    ],
    [{ kind: 'operation', ids: [] }, [['operations']]],
  ])('maps %j to the keys it invalidates', (notice, expected) => {
    expect(invalidationsFor(notice)).toEqual(expected);
  });
});

describe('startChangeStream', () => {
  let sources: FakeEventSource[];
  let queryClient: QueryClient;
  let invalidate: MockInstance<QueryClient['invalidateQueries']>;
  let states: ConnectionState[];
  let stop: () => void;

  beforeEach(() => {
    vi.useFakeTimers();
    sources = [];
    queryClient = new QueryClient();
    invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    states = [];
    stop = startChangeStream({
      url: '/api/events',
      queryClient,
      onStatus: (s: ConnectionState) => states.push(s),
      eventSourceFactory: (url: string) => {
        const source = new FakeEventSource(url);
        sources.push(source);
        return source;
      },
      coalesceMs: 150,
      reconnectMs: 1000,
    });
  });

  afterEach(() => {
    stop();
    vi.useRealTimers();
  });

  it('starts connecting, goes live on open, and a reset invalidates everything', () => {
    expect(states).toEqual(['connecting']);
    sources[0]!.open('e1');
    expect(states).toEqual(['connecting', 'live']);
    expect(invalidate).toHaveBeenCalledWith();
  });

  it('coalesces the change notices of one window into one invalidation per key', () => {
    sources[0]!.open('e1');
    invalidate.mockClear();
    sources[0]!.emit('change', { kind: 'task', ids: ['p/1'] });
    sources[0]!.emit('change', { kind: 'task', ids: ['p/1', 'p/2'] });
    expect(invalidate).not.toHaveBeenCalled();
    vi.advanceTimersByTime(150);
    const keysCalled = invalidate.mock.calls.map((c) => (c[0] as { queryKey: unknown }).queryKey);
    expect(new Set(keysCalled)).toEqual(
      new Set([keys.tasks.list(), keys.tasks.detail('p/1'), keys.tasks.detail('p/2')]),
    );
    expect(keysCalled).toHaveLength(3);
  });

  it('a notice storm neither restarts a slow fetch nor loses the last notice', async () => {
    // Each fetch takes 200 ms and answers with the notices seen when it began.
    let seen = 0;
    let calls = 0;
    const observer = new QueryObserver(queryClient, {
      queryKey: keys.host(),
      queryFn: async () => {
        calls += 1;
        const value = seen;
        await new Promise((resolve) => setTimeout(resolve, 200));
        return value;
      },
    });
    const unsubscribe = observer.subscribe(() => {});
    sources[0]!.open('e1');
    const notices = 20;
    const MIDWAY = 11; // t = 600 ms: three fetches have had time to finish.
    let midStorm: number | undefined;
    for (let i = 0; i < notices; i += 1) {
      seen += 1;
      sources[0]!.emit('change', { kind: 'host', ids: ['agent-host'] });
      await vi.advanceTimersByTimeAsync(50);
      if (i === MIDWAY) midStorm = observer.getCurrentResult().data;
    }
    await vi.advanceTimersByTimeAsync(2000);

    // A storm that restarts the fetch at each flush never lets one finish.
    expect(midStorm).toBeGreaterThan(0);
    expect(observer.getCurrentResult().data).toBe(notices);
    expect(calls).toBeLessThanOrEqual(notices / 2);
    unsubscribe();
  });

  it('a follow-up does not fire once the stream stops', async () => {
    let calls = 0;
    const observer = new QueryObserver(queryClient, {
      queryKey: keys.host(),
      queryFn: async () => {
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 200));
        return calls;
      },
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.advanceTimersByTimeAsync(0);
    sources[0]!.emit('change', { kind: 'host', ids: ['agent-host'] });
    await vi.advanceTimersByTimeAsync(150);
    const before = calls;
    stop();
    await vi.advanceTimersByTimeAsync(2000);
    expect(calls).toBe(before);
    unsubscribe();
  });

  it('reports reconnecting while the browser retries, and live again on the next open', () => {
    sources[0]!.open('e1');
    sources[0]!.fail('connecting');
    expect(states.at(-1)).toBe('reconnecting');
    expect(sources).toHaveLength(1);
    invalidate.mockClear();
    sources[0]!.open('e1');
    expect(states.at(-1)).toBe('live');
    // The reset that follows every open is what refetches what was missed.
    expect(invalidate).toHaveBeenCalledWith();
  });

  it('re-creates a closed source after the backoff, doubling it to 30 s', () => {
    sources[0]!.open('e1');
    sources[0]!.fail('closed');
    expect(states.at(-1)).toBe('reconnecting');
    vi.advanceTimersByTime(999);
    expect(sources).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sources).toHaveLength(2);
    sources[1]!.fail('closed');
    vi.advanceTimersByTime(2000);
    expect(sources).toHaveLength(3);
    for (let i = 3; i < 12; i += 1) {
      sources[i - 1]!.fail('closed');
      vi.advanceTimersByTime(30_000);
    }
    expect(sources).toHaveLength(12);
  });

  it('stops re-creating once stopped', () => {
    sources[0]!.fail('closed');
    stop();
    vi.advanceTimersByTime(60_000);
    expect(sources).toHaveLength(1);
    expect(sources[0]!.readyState).toBe(FakeEventSource.CLOSED);
  });
});
