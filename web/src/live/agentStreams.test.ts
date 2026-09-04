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
});
