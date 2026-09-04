import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { keys } from '../api/keys';
import { FakeEventSource } from '../test/fakeEventSource';
import type { ChangeNotice } from '../api/types';
import type { ConnectionState } from './changeStream';
import { invalidationsFor, startChangeStream } from './changeStream';

describe('invalidationsFor', () => {
  it.each<[ChangeNotice, unknown[]]>([
    [{ kind: 'project', ids: ['p'] }, [keys.projects()]],
    [{ kind: 'worktree', ids: ['w'] }, [keys.worktrees()]],
    [{ kind: 'desk', ids: ['task:a'] }, [keys.desk()]],
    [{ kind: 'attention', ids: ['at1'] }, [keys.attention()]],
    [
      { kind: 'task', ids: ['p/1', 'p/2'] },
      [keys.tasks.list(), keys.tasks.detail('p/1'), keys.tasks.detail('p/2')],
    ],
    [{ kind: 'task', ids: [] }, [keys.tasks.all()]],
    [{ kind: 'agent', ids: ['ag1'] }, [keys.agents.list(), keys.agents.detail('ag1')]],
    [{ kind: 'document', ids: ['d1'] }, [keys.documents.list(), keys.documents.detail('d1')]],
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
