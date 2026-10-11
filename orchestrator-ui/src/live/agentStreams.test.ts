import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TranscriptItem } from '../protocol/transcript';
import { FakeSocket } from '../fake/fakeSocket';
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
/** The daemon's echo of a sent message: a real user message, any text. */
const echo = (seq: number, markdown: string) => ({
  seq,
  event: { type: 'transcript.append', agentId: 'ag1', item: { ...item(`echo-${seq}`), markdown } },
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
  /** The `onSeen` of each agent the manager watches, after a 4404. */
  let watching: Map<string, () => void>;

  beforeEach(() => {
    vi.useFakeTimers();
    sockets = [];
    watching = new Map();
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
      watchAgent: (agentId, onSeen) => {
        watching.set(agentId, onSeen);
        return () => watching.delete(agentId);
      },
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

  it('a partial grows a partial message and leaves the cursor', () => {
    const writing: TranscriptItem = {
      id: 'a',
      ts: '',
      type: 'message',
      role: 'assistant',
      markdown: '',
      partial: true,
    };
    const partial = (markdown: string) => ({
      type: 'transcript.partial',
      agentId: 'ag1',
      itemId: 'a',
      markdown,
    });
    streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({
      type: 'transcript.snapshot',
      seq: 1,
      items: [writing],
      truncatedBefore: false,
    });
    sockets[0]!.receive(partial('All true tea'));
    expect(store.state['ag1']).toMatchObject({
      items: [{ ...writing, markdown: 'All true tea' }],
      cursor: 1,
    });
    // A partial that follows the close is stale: the whole text is in.
    sockets[0]!.receive({
      seq: 2,
      event: {
        type: 'transcript.update',
        agentId: 'ag1',
        itemId: 'a',
        patch: { markdown: 'All true tea comes from one plant.', partial: false },
      },
    });
    const closed = store.state['ag1'];
    expect(closed).toMatchObject({
      items: [{ ...writing, markdown: 'All true tea comes from one plant.', partial: false }],
      cursor: 2,
    });
    sockets[0]!.receive(partial('All true tea comes'));
    expect(store.state['ag1']).toBe(closed);
  });

  it('a frame at or below the cursor changes nothing', () => {
    streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({ type: 'transcript.snapshot', seq: 5, items: [], truncatedBefore: false });
    sockets[0]!.receive(append(6, 'a'));
    const before = store.state['ag1'];
    sockets[0]!.receive(append(6, 'a'));
    sockets[0]!.receive(append(4, 'old'));
    expect(store.state['ag1']).toBe(before);
    sockets[0]!.receive({
      type: 'transcript.replay',
      seq: 7,
      frames: [append(5, 'old'), append(6, 'a'), append(7, 'b')],
    });
    expect(store.state['ag1']).toMatchObject({ items: [item('a'), item('b')], cursor: 7 });
  });

  it('steps over a frame it does not know, and keeps the transcript', () => {
    streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({ type: 'transcript.snapshot', seq: 1, items: [], truncatedBefore: false });
    sockets[0]!.receive(append(2, 'a'));
    // A newer server's event. The cursor still moves, so a reconnect does not
    // ask for the frame again.
    sockets[0]!.receive({ seq: 3, event: { type: 'transcript.later', agentId: 'ag1' } });
    expect(store.state['ag1']).toEqual({
      items: [item('a')],
      truncatedBefore: false,
      cursor: 3,
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

  it('a lagging close reconnects from its cursor, backing off while it keeps lagging', () => {
    streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({ type: 'transcript.snapshot', seq: 5, items: [], truncatedBefore: false });
    for (const wait of [250, 500, 1000]) {
      const last = sockets.at(-1)!;
      last.serverClose(4409);
      vi.advanceTimersByTime(wait - 1);
      expect(sockets.at(-1)).toBe(last);
      vi.advanceTimersByTime(1);
      expect(sockets.at(-1)!.from).toBe(5);
      sockets.at(-1)!.open();
    }
    // A socket that stayed open 30 s has recovered: the next lag starts again.
    vi.advanceTimersByTime(30_000);
    const recovered = sockets.at(-1)!;
    recovered.serverClose(4409);
    vi.advanceTimersByTime(250);
    expect(sockets.at(-1)).not.toBe(recovered);
  });

  it('an unknown agent ends the stream until the agent list names it again', () => {
    streams.acquire('ag1');
    sockets[0]!.open();
    sockets[0]!.receive({ type: 'transcript.snapshot', seq: 3, items: [], truncatedBefore: false });
    sockets[0]!.serverClose(4404);
    expect(store.state['ag1']!.status).toBe('ended');
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);

    watching.get('ag1')!();
    expect(watching.has('ag1')).toBe(false);
    expect(sockets).toHaveLength(2);
    expect(sockets[1]!.from).toBe(3);
    expect(store.state['ag1']!.status).toBe('connecting');
  });

  it('stops watching an ended agent once its stream closes', () => {
    const release = streams.acquire('ag1');
    sockets[0]!.serverClose(4404);
    release();
    vi.advanceTimersByTime(5000);
    expect(watching.has('ag1')).toBe(false);
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
      const { remove } = streams.sendLocal('ag1', 'hello');
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
      const first = streams.sendLocal('ag1', 'hello');
      streams.dispose();
      const replacement = createAgentStreams({
        store,
        socketFactory: (url) => new FakeSocket(url),
      });
      replacement.sendLocal('ag1', 'hello');
      const second = store.state['ag1']!.items[1];

      first.remove();
      expect(store.state['ag1']!.items).toEqual([second]);
    });

    // The daemon's echo need not repeat the text: a slash command or an
    // attachment comes back in another shape.
    it('is dropped by a confirm and then an echo of any text', () => {
      const { confirm } = streams.sendLocal('ag1', 'hello');
      confirm();
      expect(store.state['ag1']!.items).toHaveLength(1);
      sockets[0]!.receive(echo(2, 'hello [image]'));
      expect(store.state['ag1']).toMatchObject({
        items: [{ ...item('echo-2'), markdown: 'hello [image]' }],
        cursor: 2,
      });
    });

    it('is dropped by an echo and then a confirm', () => {
      const { confirm } = streams.sendLocal('ag1', 'hello');
      sockets[0]!.receive(echo(2, 'hello'));
      expect(store.state['ag1']!.items).toHaveLength(2);
      confirm();
      expect(store.state['ag1']!.items).toEqual([{ ...item('echo-2'), markdown: 'hello' }]);
    });

    // `/compact` echoes as its boundary, and a skill command as its skill.
    // Neither leaves a stand-in behind for the next message's echo to take.
    it.each<[string, TranscriptItem]>([
      [
        '/compact',
        { id: 'boundary', ts: '', type: 'compact', trigger: 'manual', preTokens: 9, postTokens: 1 },
      ],
      ['/mael', { id: 'skill', ts: '', type: 'skill', skill: 'mael', markdown: 'Base directory' }],
    ])('sent as %s, is dropped by the item its command makes', (command, made) => {
      streams.sendLocal('ag1', command).confirm();
      sockets[0]!.receive({
        seq: 2,
        event: { type: 'transcript.append', agentId: 'ag1', item: made },
      });
      const next = streams.sendLocal('ag1', 'after');
      next.confirm();
      sockets[0]!.receive(echo(3, 'after'));
      expect(store.state['ag1']!.items).toEqual([made, { ...item('echo-3'), markdown: 'after' }]);
    });

    it('stays while only the echo has come', () => {
      streams.sendLocal('ag1', 'hello');
      const standIn = store.state['ag1']!.items[0]!;
      sockets[0]!.receive(echo(2, 'hello'));
      expect(store.state['ag1']!.items).toEqual([
        standIn,
        { ...item('echo-2'), markdown: 'hello' },
      ]);
    });

    it('takes the oldest stand-in for an echo', () => {
      const first = streams.sendLocal('ag1', 'one');
      const second = streams.sendLocal('ag1', 'two');
      const [, secondItem] = store.state['ag1']!.items;
      first.confirm();
      second.confirm();
      sockets[0]!.receive(echo(2, 'one'));
      expect(store.state['ag1']!.items).toEqual([
        secondItem,
        { ...item('echo-2'), markdown: 'one' },
      ]);
    });

    it('is dropped by an echo that comes in a replay', () => {
      streams.sendLocal('ag1', 'hello').confirm();
      sockets[0]!.receive({ type: 'transcript.replay', seq: 6, frames: [echo(6, 'hello')] });
      expect(store.state['ag1']!.items).toEqual([{ ...item('echo-6'), markdown: 'hello' }]);
    });

    it('is dropped by a snapshot', () => {
      streams.sendLocal('ag1', 'hello');
      sockets[0]!.receive({
        type: 'transcript.snapshot',
        seq: 5,
        items: [{ ...item('echo'), markdown: 'hello' }],
        truncatedBefore: false,
      });
      expect(store.state['ag1']!.items).toEqual([{ ...item('echo'), markdown: 'hello' }]);
    });
  });
});
