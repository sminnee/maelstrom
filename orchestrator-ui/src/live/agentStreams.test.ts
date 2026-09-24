import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TranscriptItem } from '../protocol/transcript';
import { FakeSocket } from '../test/fakeSocket';
import { createAgentStreams, type AgentStreams, type TranscriptStore } from './agentStreams';
import type { TranscriptState } from './transcriptReducer';

const item = (id: string): TranscriptItem => ({
  id,
  ts: '',
  type: 'message',
  role: 'user',
  markdown: id,
});
const append = (seq: number, id: string) => ({
  seq,
  event: { type: 'transcript.append', agentId: 'ag1', item: item(id) },
});

/** A store the manager writes to, held as a plain map. */
function memoryStore(): TranscriptStore & { state: Record<string, TranscriptState> } {
  const state: Record<string, TranscriptState> = {};
  return {
    state,
    get: (agentId) => state[agentId],
    set: (agentId, next) => {
      state[agentId] = next;
    },
    drop: (agentId) => {
      delete state[agentId];
    },
  };
}

describe('agent streams', () => {
  let sockets: FakeSocket[];
  let store: ReturnType<typeof memoryStore>;
  let streams: AgentStreams;

  beforeEach(() => {
    vi.useFakeTimers();
    sockets = [];
    store = memoryStore();
    streams = createAgentStreams({
      store,
      socketFactory: (url) => {
        const socket = new FakeSocket(url);
        sockets.push(socket);
        return socket;
      },
      reconnectMs: 1000,
      graceMs: 5000,
    });
  });

  afterEach(() => vi.useRealTimers());

  it('opens one socket on acquire and takes the snapshot as the transcript', () => {
    streams.acquire('ag1');
    expect(sockets).toHaveLength(1);
    expect(sockets[0]!.url).toBe('/api/agents/ag1/stream');
    expect(store.state['ag1']).toMatchObject({ items: [], cursor: 0, status: 'connecting' });
    sockets[0]!.open();
    sockets[0]!.receive({
      type: 'transcript.snapshot',
      seq: 7,
      items: [item('a')],
      truncatedBefore: true,
    });
    expect(store.state['ag1']).toEqual({
      items: [item('a')],
      truncatedBefore: true,
      cursor: 7,
      status: 'live',
    });
  });

  it('reduces live frames and moves the cursor', () => {
    streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({ type: 'transcript.snapshot', seq: 1, items: [], truncatedBefore: false });
    sockets[0]!.receive(append(2, 'a'));
    sockets[0]!.receive({
      seq: 3,
      event: { type: 'transcript.update', agentId: 'ag1', itemId: 'a', patch: { markdown: 'b' } },
    });
    sockets[0]!.receive({ seq: 4, event: { type: 'transcript.truncated', agentId: 'ag1' } });
    expect(store.state['ag1']).toEqual({
      items: [{ ...item('a'), markdown: 'b' }],
      truncatedBefore: true,
      cursor: 4,
      status: 'live',
    });
  });

  it('reconnects from its cursor after a drop, and applies the replay', () => {
    streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({ type: 'transcript.snapshot', seq: 5, items: [], truncatedBefore: false });
    sockets[0]!.serverClose();
    expect(store.state['ag1']!.status).toBe('reconnecting');
    vi.advanceTimersByTime(1000);
    expect(sockets).toHaveLength(2);
    expect(sockets[1]!.from).toBe(5);
    sockets[1]!.open();
    sockets[1]!.receive({
      type: 'transcript.replay',
      seq: 7,
      frames: [append(6, 'a'), append(7, 'b')],
    });
    expect(store.state['ag1']).toMatchObject({
      items: [item('a'), item('b')],
      cursor: 7,
      status: 'live',
    });
  });

  it('a lagging close reconnects at once from its cursor', () => {
    streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({ type: 'transcript.snapshot', seq: 5, items: [], truncatedBefore: false });
    sockets[0]!.serverClose(4409);
    vi.advanceTimersByTime(0);
    expect(sockets).toHaveLength(2);
    expect(sockets[1]!.from).toBe(5);
  });

  it('an unknown agent ends the stream and never reconnects', () => {
    streams.acquire('ag1');
    sockets[0]!.serverClose(4404);
    expect(store.state['ag1']!.status).toBe('ended');
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);
  });

  it('two acquires share one socket, and the last release closes it after the grace', () => {
    const first = streams.acquire('ag1');
    const second = streams.acquire('ag1');
    expect(sockets).toHaveLength(1);
    first();
    vi.advanceTimersByTime(5000);
    expect(sockets[0]!.closed).toBe(false);
    second();
    vi.advanceTimersByTime(4999);
    expect(sockets[0]!.closed).toBe(false);
    vi.advanceTimersByTime(1);
    expect(sockets[0]!.closed).toBe(true);
    expect(store.state['ag1']).toBeUndefined();
  });

  it('a re-acquire inside the grace keeps the socket', () => {
    const release = streams.acquire('ag1');
    release();
    vi.advanceTimersByTime(4000);
    streams.acquire('ag1');
    vi.advanceTimersByTime(5000);
    expect(sockets[0]!.closed).toBe(false);
    expect(sockets).toHaveLength(1);
  });

  it('dispose closes the sockets and disarms the grace, leaving the store alone', () => {
    // The manager outlives the views that used it: a release only arms a
    // grace timer. Whoever owns the manager disposes it, and that must not
    // reach a store which by then belongs to someone else.
    const release = streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({
      type: 'transcript.snapshot',
      seq: 1,
      items: [item('m1')],
      truncatedBefore: false,
    });
    release();

    streams.dispose();
    expect(sockets[0]!.closed).toBe(true);
    // Left for the next reader of the store, not dropped.
    expect(store.state['ag1']?.items).toHaveLength(1);

    // The armed grace timer must not fire after dispose.
    store.state['ag1'] = { ...store.state['ag1']! };
    vi.advanceTimersByTime(60_000);
    expect(store.state['ag1']).toBeDefined();
  });

  it('dispose disarms a pending reconnect', () => {
    streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.serverClose(1006);
    expect(sockets).toHaveLength(1);

    streams.dispose();
    vi.advanceTimersByTime(60_000);
    // No reconnect: the manager is gone, so nothing reopens for it.
    expect(sockets).toHaveLength(1);
  });

  it('a re-acquire after the grace opens a new socket and refills the store', () => {
    const release = streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({
      type: 'transcript.snapshot',
      seq: 3,
      items: [],
      truncatedBefore: false,
    });
    release();
    vi.advanceTimersByTime(5000);
    expect(sockets[0]!.closed).toBe(true);
    expect(store.state['ag1']).toBeUndefined();

    streams.acquire('ag1');
    expect(sockets).toHaveLength(2);
    expect(sockets[1]!.url).toBe('/api/agents/ag1/stream');
    expect(store.state['ag1']).toMatchObject({ status: 'connecting' });
  });

  describe('a local stand-in', () => {
    beforeEach(() => {
      streams.acquire('ag1');
      sockets[0]!.open();
      sockets[0]!.receive({
        type: 'transcript.snapshot',
        seq: 1,
        items: [],
        truncatedBefore: false,
      });
    });

    it('shows at once and its remover drops it again', () => {
      const remove = streams.sendLocal('ag1', 'hello');
      expect(store.state['ag1']!.items).toHaveLength(1);
      expect(store.state['ag1']!.items[0]).toMatchObject({
        role: 'user',
        markdown: 'hello',
        pending: true,
      });

      remove();
      expect(store.state['ag1']!.items).toHaveLength(0);
    });

    it('drops only its own stand-in, even after a replacement manager takes the store', () => {
      const removeFirst = streams.sendLocal('ag1', 'hello');
      streams.dispose();
      const replacement = createAgentStreams({
        store,
        socketFactory: (url) => new FakeSocket(url),
      });
      replacement.sendLocal('ag1', 'hello');
      const second = store.state['ag1']!.items[1];

      removeFirst();
      expect(store.state['ag1']!.items).toEqual([second]);
    });

    it('is dropped once a matching append lands, and the append still moves the cursor', () => {
      streams.sendLocal('ag1', 'hello');
      sockets[0]!.receive({
        seq: 2,
        event: {
          type: 'transcript.append',
          agentId: 'ag1',
          item: { ...item('echo'), markdown: 'hello' },
        },
      });
      expect(store.state['ag1']).toMatchObject({
        items: [{ ...item('echo'), markdown: 'hello' }],
        cursor: 2,
      });
    });

    it('is left alone by an append whose markdown does not match', () => {
      streams.sendLocal('ag1', 'hello');
      const standIn = store.state['ag1']!.items[0]!;
      sockets[0]!.receive(append(2, 'unrelated'));
      expect(store.state['ag1']!.items).toEqual([standIn, item('unrelated')]);
    });

    it('the oldest of two matching stand-ins is the one an echo drops', () => {
      streams.sendLocal('ag1', 'hello');
      streams.sendLocal('ag1', 'hello');
      const [first, second] = store.state['ag1']!.items;

      sockets[0]!.receive({
        seq: 2,
        event: {
          type: 'transcript.append',
          agentId: 'ag1',
          item: { ...item('echo'), markdown: 'hello' },
        },
      });

      expect(store.state['ag1']!.items).toEqual([second, { ...item('echo'), markdown: 'hello' }]);
      expect(store.state['ag1']!.items).not.toContainEqual(first);
    });

    it('is dropped when the arriving item comes from a snapshot', () => {
      streams.sendLocal('ag1', 'hello');
      sockets[0]!.receive({
        type: 'transcript.snapshot',
        seq: 5,
        items: [{ ...item('echo'), markdown: 'hello' }],
        truncatedBefore: false,
      });
      expect(store.state['ag1']!.items).toEqual([{ ...item('echo'), markdown: 'hello' }]);
    });

    it('is dropped when the arriving item comes from a replay', () => {
      streams.sendLocal('ag1', 'hello');
      sockets[0]!.receive({
        type: 'transcript.replay',
        seq: 6,
        frames: [
          {
            seq: 6,
            event: {
              type: 'transcript.append',
              agentId: 'ag1',
              item: { ...item('echo'), markdown: 'hello' },
            },
          },
        ],
      });
      expect(store.state['ag1']!.items).toEqual([{ ...item('echo'), markdown: 'hello' }]);
    });
  });
});
