import { describe, expect, it } from 'vitest';
import { createWsBackend, type SocketLike } from './wsBackend';
import type { EventFrame } from '../protocol/events';
import { emptyWorld } from '../protocol/reducer';

/** A socket the test drives by hand: what the page sent, and what the server says back. */
class FakeSocket implements SocketLike {
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  /** The server side. */
  open() {
    this.onopen?.();
  }
  receive(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  drop() {
    this.onclose?.();
  }
  lastSent(): Record<string, unknown> {
    return JSON.parse(this.sent[this.sent.length - 1]!) as Record<string, unknown>;
  }
}

function frame(seq: number, event: EventFrame['event']): EventFrame {
  return { seq, ts: '2026-09-01T00:00:00Z', event };
}

const snapshot = (seq: number) =>
  frame(seq, { type: 'snapshot', world: emptyWorld(), transcripts: {} });

function harness(reconnectMs = 0) {
  const sockets: FakeSocket[] = [];
  const backend = createWsBackend({
    url: 'ws://test',
    socketFactory: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
    reconnectMs,
  });
  const frames: EventFrame[] = [];
  backend.subscribe((f) => frames.push(f));
  return { backend, sockets, frames };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

describe('the WebSocket backend honours the Backend contract', () => {
  it('connect says hello and resolves on ready, delivering the snapshot first', async () => {
    const { backend, sockets, frames } = harness();
    const connecting = backend.connect();
    const socket = sockets[0]!;
    socket.open();
    expect(socket.lastSent()).toEqual({ type: 'hello' });
    socket.receive(snapshot(3));
    socket.receive({ ready: { seq: 3 } });
    await connecting;
    expect(frames.map((f) => f.seq)).toEqual([3]);
    expect(frames[0]?.event.type).toBe('snapshot');
  });

  it('a command is correlated to its reply by id', async () => {
    const { backend, sockets } = harness();
    const connecting = backend.connect();
    const socket = sockets[0]!;
    socket.open();
    socket.receive(snapshot(1));
    socket.receive({ ready: { seq: 1 } });
    await connecting;
    const pending = backend.command({ type: 'agent.stop', agentId: 'a1' });
    const sent = socket.lastSent() as { id: string; command: unknown };
    expect(sent.command).toEqual({ type: 'agent.stop', agentId: 'a1' });
    socket.receive({ reply: { id: 'other', ok: true, result: {} } });
    socket.receive({
      reply: { id: sent.id, ok: false, error: { code: 'unknown_id', message: 'no' } },
    });
    expect(await pending).toEqual({ ok: false, error: { code: 'unknown_id', message: 'no' } });
  });

  it('an unexpected close reconnects and resumes from the last seq', async () => {
    const { backend, sockets, frames } = harness();
    const connecting = backend.connect();
    sockets[0]!.open();
    sockets[0]!.receive(snapshot(4));
    sockets[0]!.receive({ ready: { seq: 4 } });
    await connecting;
    sockets[0]!.receive(frame(5, { type: 'remove', kind: 'task', id: 'T-1' }));
    sockets[0]!.drop();
    await tick();
    expect(sockets).toHaveLength(2);
    sockets[1]!.open();
    expect(sockets[1]!.lastSent()).toEqual({ type: 'hello', resumeFrom: 5 });
    sockets[1]!.receive(frame(6, { type: 'remove', kind: 'task', id: 'T-2' }));
    sockets[1]!.receive({ ready: { seq: 6 } });
    await tick();
    expect(frames.map((f) => f.seq)).toEqual([4, 5, 6]);
  });

  it('a command in flight when the socket drops rejects, as a transport failure', async () => {
    const { backend, sockets } = harness();
    const connecting = backend.connect();
    sockets[0]!.open();
    sockets[0]!.receive(snapshot(1));
    sockets[0]!.receive({ ready: { seq: 1 } });
    await connecting;
    const pending = backend.command({ type: 'agent.stop', agentId: 'a1' });
    sockets[0]!.drop();
    await expect(pending).rejects.toThrow(/closed/);
  });

  it('a restarted server answers a resume with a lower-seq snapshot, which lands', async () => {
    const { backend, sockets, frames } = harness();
    const connecting = backend.connect();
    sockets[0]!.open();
    sockets[0]!.receive(snapshot(4));
    sockets[0]!.receive({ ready: { seq: 4 } });
    await connecting;
    sockets[0]!.receive(frame(5, { type: 'remove', kind: 'task', id: 'T-1' }));
    sockets[0]!.drop();
    await tick();
    sockets[1]!.open();
    expect(sockets[1]!.lastSent()).toEqual({ type: 'hello', resumeFrom: 5 });
    sockets[1]!.receive(snapshot(1));
    sockets[1]!.receive({ ready: { seq: 1 } });
    sockets[1]!.receive(frame(2, { type: 'remove', kind: 'task', id: 'T-2' }));
    await tick();
    expect(frames.map((f) => f.seq)).toEqual([4, 5, 1, 2]);
  });

  it('a bare connect after a close asks for a fresh snapshot, not a replay', async () => {
    const { backend, sockets } = harness();
    const connecting = backend.connect();
    sockets[0]!.open();
    sockets[0]!.receive(snapshot(7));
    sockets[0]!.receive({ ready: { seq: 7 } });
    await connecting;
    backend.close();
    const again = backend.connect();
    sockets[1]!.open();
    expect(sockets[1]!.lastSent()).toEqual({ type: 'hello' });
    sockets[1]!.receive(snapshot(8));
    sockets[1]!.receive({ ready: { seq: 8 } });
    await again;
  });

  it('close while connecting rejects the pending connect', async () => {
    const { backend, sockets } = harness();
    const connecting = backend.connect();
    sockets[0]!.open();
    backend.close();
    await expect(connecting).rejects.toThrow(/closed/);
  });

  it('close stops reconnecting', async () => {
    const { backend, sockets } = harness();
    const connecting = backend.connect();
    sockets[0]!.open();
    sockets[0]!.receive(snapshot(1));
    sockets[0]!.receive({ ready: { seq: 1 } });
    await connecting;
    backend.close();
    await tick();
    expect(sockets[0]!.closed).toBe(true);
    expect(sockets).toHaveLength(1);
  });

  it('a second connect while connected is a no-op', async () => {
    const { backend, sockets } = harness();
    const connecting = backend.connect();
    sockets[0]!.open();
    sockets[0]!.receive(snapshot(1));
    sockets[0]!.receive({ ready: { seq: 1 } });
    await connecting;
    await backend.connect();
    expect(sockets).toHaveLength(1);
  });
});
