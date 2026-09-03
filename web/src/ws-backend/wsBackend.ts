import type { Backend } from '../protocol/backend';
import type { Command, Reply } from '../protocol/commands';
import type { EventFrame } from '../protocol/events';
import type { Seq } from '../protocol/ids';

/**
 * The part of a WebSocket the backend uses, so a test can drive a fake one.
 * A real `WebSocket` satisfies it as-is.
 */
export interface SocketLike {
  send(data: string): void;
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
}

export interface WsBackendOptions {
  url: string;
  socketFactory?: (url: string) => SocketLike;
  /** The first wait before reconnecting after an unexpected close; doubles per attempt. */
  reconnectMs?: number;
}

/** The longest wait between reconnect attempts. */
const MAX_RECONNECT_MS = 30_000;

type Pending = { resolve: (reply: Reply<Command>) => void; reject: (err: Error) => void };

/**
 * The real transport: one WebSocket to the orchestrator server, carrying
 * seq-stamped frames down and commands with correlated replies up. See
 * `docs/dev/orchestrator-server.md` for the wire format.
 *
 * `connect` says hello and resolves on `ready`. An unexpected close reconnects
 * and resumes from the last seq applied; subscriptions survive a close, as
 * they do in the fake. `close()` stops reconnecting.
 */
export function createWsBackend(opts: WsBackendOptions): Backend {
  const factory = opts.socketFactory ?? ((url: string) => new WebSocket(url) as SocketLike);
  const reconnectMs = opts.reconnectMs ?? 1000;
  const listeners = new Set<(frame: EventFrame) => void>();
  const pending = new Map<string, Pending>();
  let socket: SocketLike | null = null;
  let lastSeq: Seq = 0;
  let nextId = 1;
  let closedByUser = false;
  let ready: { resolve: () => void; reject: (err: Error) => void } | null = null;
  let connected = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let attempts = 0;

  function clearReconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function failPending(reason: string) {
    for (const p of pending.values()) p.reject(new Error(reason));
    pending.clear();
  }

  function open(resumeFrom: Seq | undefined): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      ready = { resolve, reject };
      const s = factory(opts.url);
      socket = s;
      s.onopen = () => {
        const hello: { type: 'hello'; resumeFrom?: Seq } = { type: 'hello' };
        if (resumeFrom !== undefined && resumeFrom > 0) hello.resumeFrom = resumeFrom;
        s.send(JSON.stringify(hello));
      };
      s.onmessage = (event) => {
        let message: Record<string, unknown>;
        try {
          message = JSON.parse(event.data) as Record<string, unknown>;
        } catch {
          console.warn('orchestrator: dropped a frame that is not JSON');
          return;
        }
        onMessage(message);
      };
      s.onerror = () => undefined;
      s.onclose = () => {
        if (socket !== s) return;
        socket = null;
        connected = false;
        failPending('The connection closed');
        if (ready) {
          ready.reject(new Error('The connection closed before ready'));
          ready = null;
        }
        if (!closedByUser) {
          const wait = Math.min(reconnectMs * 2 ** attempts, MAX_RECONNECT_MS);
          attempts += 1;
          reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            // A failed reconnect closes the socket again, which schedules
            // the next attempt, so nothing else has to retry.
            void open(lastSeq).catch(() => undefined);
          }, wait);
        }
      };
    });
  }

  function onMessage(message: Record<string, unknown>) {
    if ('seq' in message) {
      const frame = message as unknown as EventFrame;
      // A snapshot is a new epoch: it may carry a smaller seq than the last.
      if (frame.event.type === 'snapshot' || frame.seq > lastSeq) lastSeq = frame.seq;
      for (const l of [...listeners]) l(frame);
      return;
    }
    if ('ready' in message) {
      connected = true;
      attempts = 0;
      ready?.resolve();
      ready = null;
      return;
    }
    if ('reply' in message) {
      const reply = message.reply as { id: string } & Reply<Command>;
      const waiting = pending.get(reply.id);
      if (!waiting) {
        // The server's answer to something this page never asked: a refused
        // hello, or a reply to a command that was already failed on close.
        console.warn('orchestrator: unmatched reply', reply);
        return;
      }
      pending.delete(reply.id);
      waiting.resolve(
        reply.ok ? { ok: true, result: reply.result } : { ok: false, error: reply.error },
      );
    }
  }

  return {
    async connect(connectOpts) {
      // A bare connect asks for a fresh snapshot; only an explicit resumeFrom
      // (or the reconnect path) replays. The store resets before it connects,
      // so a replay alone would leave it holding only the frames since.
      closedByUser = false;
      clearReconnect();
      if (connected || ready) return;
      await open(connectOpts?.resumeFrom);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    command<C extends Command>(cmd: C): Promise<Reply<C>> {
      const s = socket;
      if (!s || !connected) return Promise.reject(new Error('Not connected'));
      const id = `c${nextId++}`;
      return new Promise<Reply<C>>((resolve, reject) => {
        pending.set(id, { resolve: (r) => resolve(r as Reply<C>), reject });
        s.send(JSON.stringify({ id, command: cmd }));
      });
    },
    close() {
      closedByUser = true;
      clearReconnect();
      const s = socket;
      socket = null;
      connected = false;
      s?.close();
      failPending('The connection was closed');
      if (ready) {
        ready.reject(new Error('The connection was closed'));
        ready = null;
      }
    },
  };
}
