import type { AgentId } from '../protocol/ids';
import type { MessageItem, TranscriptItem } from '../protocol/transcript';
import { browserSocket, type SocketLike } from './socketLike';
import {
  applyPartial,
  emptyTranscript,
  reduceTranscript,
  type TranscriptFrame,
  type TranscriptPartial,
  type TranscriptState,
} from './transcriptReducer';

/** Where the transcripts live: the store's slice, behind three verbs. */
export interface TranscriptStore {
  get(agentId: AgentId): TranscriptState | undefined;
  set(agentId: AgentId, state: TranscriptState): void;
  drop(agentId: AgentId): void;
}

export interface AgentStreamsOptions {
  store: TranscriptStore;
  socketFactory?: (path: string) => SocketLike;
  /** The first wait before reconnecting after an unexpected close; doubles per attempt. */
  reconnectMs?: number;
  /** How long a stream nobody shows stays open, in case a view comes back. */
  graceMs?: number;
  /**
   * Calls `onSeen` when the agent list next names the agent. A stream the
   * server ended as unknown waits on it, so an agent that the server comes to
   * know again gets its transcript back. Returns the unsubscribe.
   */
  watchAgent?: (agentId: AgentId, onSeen: () => void) => () => void;
}

/** A local stand-in's two handles. */
export interface StandIn {
  /** Drops the stand-in: the send failed. */
  remove(): void;
  /** The server took the send. The stand-in drops once its echo has come too. */
  confirm(): void;
}

export interface AgentStreams {
  /** Show an agent's transcript. Returns the release; the last release closes the stream. */
  acquire(agentId: AgentId): () => void;
  /**
   * Show a sent message before the daemon's echo confirms it reached the
   * agent.
   */
  sendLocal(agentId: AgentId, markdown: string): StandIn;
  /**
   * Drop every stream and the timers it is holding, without touching the
   * store. A release only decrements a refcount and arms a grace timer, so
   * the manager outlives the views that used it; whoever owns the manager
   * calls this when it goes, or those timers fire against a store that has
   * moved on.
   */
  dispose(): void;
}

/** The server closes with these when the agent is unknown, or the reader fell behind. */
export const CLOSE_UNKNOWN_ID = 4404;
export const CLOSE_LAGGING = 4409;

const MAX_RECONNECT_MS = 30_000;
/** The first wait after a lagging close; doubles while the reader keeps lagging. */
const LAG_RECONNECT_MS = 250;
/** How long a socket stays open before a reader that lagged counts as recovered. */
const LAG_RECOVERED_MS = 30_000;

/**
 * Numbers each stand-in bubble. Not `crypto.randomUUID`: a page served over plain HTTP
 * is not a secure context, and there the browser withholds it. Module-level, because a
 * replacement manager takes over a store that can still hold the last one's stand-ins.
 */
let localSeq = 0;

/**
 * Whether an item is the daemon's echo of a send. Most sends echo as a user
 * message. A skill command echoes as its `skill` item, and `/compact` as its
 * `compact` boundary: the summary after the boundary is not a second echo.
 */
function isEcho(item: TranscriptItem): boolean {
  if (item.type === 'message') return item.role === 'user' && !item.pending;
  return item.type === 'skill' || item.type === 'compact';
}

/**
 * What each stand-in has seen, by id. Module-level for the reason `localSeq`
 * is: a replacement manager can meet the last one's stand-ins in the store.
 */
const standIns = new Map<string, { confirmed: boolean; echoed: boolean }>();

type Opening =
  | { type: 'transcript.snapshot'; seq: number; items: TranscriptItem[]; truncatedBefore: boolean }
  | { type: 'transcript.replay'; seq: number; frames: TranscriptFrame[] };

interface Stream {
  refs: number;
  socket: SocketLike | null;
  attempts: number;
  /** Lagging closes in a row; a socket that stays open long enough resets it. */
  lagAttempts: number;
  openedAt: number;
  unwatch: (() => void) | null;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  graceTimer: ReturnType<typeof setTimeout> | null;
  ended: boolean;
}

/**
 * One transcript socket per agent, however many views show it. A view
 * acquires the agent and releases it when it goes; the socket outlives a
 * brief release, so a tab that closes and reopens keeps its stream.
 * Reconnects from its cursor on a drop. See
 * `docs/dev/orchestrator-ui.md`, "Transcripts are sockets".
 */
export function createAgentStreams(opts: AgentStreamsOptions): AgentStreams {
  const { store } = opts;
  const factory = opts.socketFactory ?? browserSocket;
  const reconnectMs = opts.reconnectMs ?? 1000;
  const graceMs = opts.graceMs ?? 5000;
  const streams = new Map<AgentId, Stream>();

  const update = (agentId: AgentId, patch: Partial<TranscriptState>) => {
    store.set(agentId, { ...(store.get(agentId) ?? emptyTranscript()), ...patch });
  };

  const dropStandIn = (state: TranscriptState, id: string): TranscriptState => {
    standIns.delete(id);
    return { ...state, items: state.items.filter((i) => i.id !== id) };
  };

  /**
   * An echo marks the oldest stand-in not yet echoed. The text is not
   * compared: the echo of a slash command or an attachment differs from what
   * was typed. A stand-in already confirmed drops.
   */
  const markEcho = (state: TranscriptState, frame: TranscriptFrame): TranscriptState => {
    if (frame.event.type !== 'transcript.append' || !isEcho(frame.event.item)) return state;
    const standIn = state.items.find(
      (i) => i.type === 'message' && i.pending && standIns.get(i.id)?.echoed === false,
    );
    const seen = standIn && standIns.get(standIn.id);
    if (!standIn || !seen) return state;
    seen.echoed = true;
    return seen.confirmed ? dropStandIn(state, standIn.id) : state;
  };

  /** One frame, unless the cursor already covers it: a replay can overlap a live frame. */
  const applyFrame = (state: TranscriptState, frame: TranscriptFrame): TranscriptState =>
    frame.seq <= state.cursor ? state : markEcho(reduceTranscript(state, frame), frame);

  const connect = (agentId: AgentId, stream: Stream) => {
    const cursor = store.get(agentId)?.cursor ?? 0;
    const query = cursor > 0 ? `?from=${cursor}` : '';
    const socket = factory(`/api/agents/${agentId}/stream${query}`);
    stream.socket = socket;
    stream.openedAt = 0;
    socket.onopen = () => {
      stream.attempts = 0;
      stream.openedAt = Date.now();
    };
    socket.onmessage = (event) => {
      let message: Opening | TranscriptFrame | TranscriptPartial;
      try {
        message = JSON.parse(event.data) as Opening | TranscriptFrame | TranscriptPartial;
      } catch {
        // A frame that is not JSON is a server bug. Skipping it would leave
        // this client behind the server with no way back; a close reconnects
        // from the cursor and takes the frame again.
        socket.close();
        return;
      }
      if ('seq' in message && 'event' in message) {
        const current = store.get(agentId) ?? emptyTranscript();
        const state = applyFrame(current, message);
        if (state !== current) store.set(agentId, { ...state, status: 'live' });
        return;
      }
      if (message.type === 'transcript.snapshot') {
        // The snapshot replaces the items, and the stand-ins go with them.
        for (const item of store.get(agentId)?.items ?? []) standIns.delete(item.id);
        store.set(agentId, {
          items: message.items,
          truncatedBefore: message.truncatedBefore,
          cursor: message.seq,
          status: 'live',
        });
      } else if (message.type === 'transcript.replay') {
        let state = store.get(agentId) ?? emptyTranscript();
        for (const frame of message.frames) state = applyFrame(state, frame);
        store.set(agentId, { ...state, cursor: message.seq, status: 'live' });
      } else if (message.type === 'transcript.partial') {
        const current = store.get(agentId);
        const next = current && applyPartial(current, message);
        // A stale partial changes nothing, so no view redraws for it.
        if (next && next !== current) store.set(agentId, next);
      }
    };
    socket.onclose = (event) => {
      if (stream.socket !== socket) return;
      stream.socket = null;
      if (stream.refs === 0 || stream.ended) return;
      if (event.code === CLOSE_UNKNOWN_ID) {
        stream.ended = true;
        update(agentId, { status: 'ended' });
        stream.unwatch =
          opts.watchAgent?.(agentId, () => {
            stream.unwatch?.();
            stream.unwatch = null;
            stream.ended = false;
            update(agentId, { status: 'connecting' });
            connect(agentId, stream);
          }) ?? null;
        return;
      }
      update(agentId, { status: 'reconnecting' });
      if (stream.openedAt && Date.now() - stream.openedAt >= LAG_RECOVERED_MS)
        stream.lagAttempts = 0;
      let wait: number;
      if (event.code === CLOSE_LAGGING) {
        wait = Math.min(LAG_RECONNECT_MS * 2 ** stream.lagAttempts, MAX_RECONNECT_MS);
        stream.lagAttempts += 1;
      } else {
        wait = Math.min(reconnectMs * 2 ** stream.attempts, MAX_RECONNECT_MS);
        stream.attempts += 1;
      }
      stream.reconnectTimer = setTimeout(() => {
        stream.reconnectTimer = null;
        connect(agentId, stream);
      }, wait);
    };
  };

  const close = (agentId: AgentId, stream: Stream) => {
    stream.unwatch?.();
    stream.unwatch = null;
    if (stream.reconnectTimer) clearTimeout(stream.reconnectTimer);
    if (stream.graceTimer) clearTimeout(stream.graceTimer);
    const socket = stream.socket;
    stream.socket = null;
    socket?.close();
    streams.delete(agentId);
    store.drop(agentId);
  };

  return {
    acquire(agentId) {
      let stream = streams.get(agentId);
      if (!stream) {
        stream = {
          refs: 0,
          socket: null,
          attempts: 0,
          lagAttempts: 0,
          openedAt: 0,
          unwatch: null,
          reconnectTimer: null,
          graceTimer: null,
          ended: false,
        };
        streams.set(agentId, stream);
        store.set(agentId, emptyTranscript('connecting'));
        connect(agentId, stream);
      }
      if (stream.graceTimer) {
        clearTimeout(stream.graceTimer);
        stream.graceTimer = null;
      }
      stream.refs += 1;
      let released = false;
      return () => {
        if (released) return;
        released = true;
        stream.refs -= 1;
        if (stream.refs > 0) return;
        stream.graceTimer = setTimeout(() => {
          stream.graceTimer = null;
          if (stream.refs === 0) close(agentId, stream);
        }, graceMs);
      };
    },
    sendLocal(agentId, markdown) {
      const id = `local-${++localSeq}`;
      const item: MessageItem = {
        id,
        ts: new Date().toISOString(),
        type: 'message',
        role: 'user',
        markdown,
        pending: true,
      };
      const state = store.get(agentId) ?? emptyTranscript();
      const seen = { confirmed: false, echoed: false };
      standIns.set(id, seen);
      store.set(agentId, { ...state, items: [...state.items, item] });
      const drop = () => {
        const current = store.get(agentId);
        if (current) store.set(agentId, dropStandIn(current, id));
        else standIns.delete(id);
      };
      return {
        remove: drop,
        confirm: () => {
          seen.confirmed = true;
          if (seen.echoed) drop();
        },
      };
    },
    dispose() {
      for (const stream of streams.values()) {
        stream.unwatch?.();
        stream.unwatch = null;
        if (stream.reconnectTimer) clearTimeout(stream.reconnectTimer);
        if (stream.graceTimer) clearTimeout(stream.graceTimer);
        const socket = stream.socket;
        stream.socket = null;
        socket?.close();
      }
      // The store is not this manager's to clear. `close` drops a transcript
      // nobody is showing; a disposed manager is being replaced, and dropping
      // here would take the transcript from whoever reads the store next.
      streams.clear();
    },
  };
}
