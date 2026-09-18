import type { AgentId } from '../protocol/ids';
import type { MessageItem, TranscriptItem } from '../protocol/transcript';
import { browserSocket, type SocketLike } from './socketLike';
import {
  emptyTranscript,
  reduceTranscript,
  type TranscriptFrame,
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
}

export interface AgentStreams {
  /** Show an agent's transcript. Returns the release; the last release closes the stream. */
  acquire(agentId: AgentId): () => void;
  /**
   * Show a sent message before the daemon's echo confirms it reached the
   * agent. Returns a remover, for a failed send.
   */
  sendLocal(agentId: AgentId, markdown: string): () => void;
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

type Opening =
  | { type: 'transcript.snapshot'; seq: number; items: TranscriptItem[]; truncatedBefore: boolean }
  | { type: 'transcript.replay'; seq: number; frames: TranscriptFrame[] };

interface Stream {
  refs: number;
  socket: SocketLike | null;
  attempts: number;
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

  /**
   * Drop the oldest local stand-in that matches a just-arrived real user
   * message, so the two never coexist as duplicate bubbles. At most one
   * stand-in is in flight per send, so a single match is enough.
   */
  const dropEchoedStandIn = (state: TranscriptState, item: TranscriptItem): TranscriptState => {
    if (item.type !== 'message' || item.role !== 'user' || item.pending) return state;
    const index = state.items.findIndex(
      (i) => i.type === 'message' && i.pending && i.markdown === item.markdown,
    );
    if (index < 0) return state;
    return { ...state, items: state.items.toSpliced(index, 1) };
  };

  /** `dropEchoedStandIn`, applied only when the frame is the kind it acts on. */
  const reconcileFrame = (state: TranscriptState, frame: TranscriptFrame): TranscriptState =>
    frame.event.type === 'transcript.append' ? dropEchoedStandIn(state, frame.event.item) : state;

  const connect = (agentId: AgentId, stream: Stream) => {
    const cursor = store.get(agentId)?.cursor ?? 0;
    const query = cursor > 0 ? `?from=${cursor}` : '';
    const socket = factory(`/api/agents/${agentId}/stream${query}`);
    stream.socket = socket;
    socket.onopen = () => {
      stream.attempts = 0;
    };
    socket.onmessage = (event) => {
      let message: Opening | TranscriptFrame;
      try {
        message = JSON.parse(event.data) as Opening | TranscriptFrame;
      } catch {
        // A frame that is not JSON is a server bug. Skipping it would leave
        // this client behind the server with no way back; a close reconnects
        // from the cursor and takes the frame again.
        socket.close();
        return;
      }
      if ('seq' in message && 'event' in message) {
        const current = store.get(agentId) ?? emptyTranscript();
        const state = reconcileFrame(reduceTranscript(current, message), message);
        store.set(agentId, { ...state, status: 'live' });
        return;
      }
      if (message.type === 'transcript.snapshot') {
        let state: TranscriptState = {
          items: message.items,
          truncatedBefore: message.truncatedBefore,
          cursor: message.seq,
          status: 'live',
        };
        for (const item of message.items) state = dropEchoedStandIn(state, item);
        store.set(agentId, state);
      } else if (message.type === 'transcript.replay') {
        let state = store.get(agentId) ?? emptyTranscript();
        for (const frame of message.frames) {
          state = reconcileFrame(reduceTranscript(state, frame), frame);
        }
        store.set(agentId, { ...state, cursor: message.seq, status: 'live' });
      }
    };
    socket.onclose = (event) => {
      if (stream.socket !== socket) return;
      stream.socket = null;
      if (stream.refs === 0 || stream.ended) return;
      if (event.code === CLOSE_UNKNOWN_ID) {
        stream.ended = true;
        update(agentId, { status: 'ended' });
        return;
      }
      update(agentId, { status: 'reconnecting' });
      const wait =
        event.code === CLOSE_LAGGING
          ? 0
          : Math.min(reconnectMs * 2 ** stream.attempts, MAX_RECONNECT_MS);
      stream.attempts += 1;
      stream.reconnectTimer = setTimeout(() => {
        stream.reconnectTimer = null;
        connect(agentId, stream);
      }, wait);
    };
  };

  const close = (agentId: AgentId, stream: Stream) => {
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
      const id = `local-${crypto.randomUUID()}`;
      const item: MessageItem = {
        id,
        ts: new Date().toISOString(),
        type: 'message',
        role: 'user',
        markdown,
        pending: true,
      };
      const state = store.get(agentId) ?? emptyTranscript();
      store.set(agentId, { ...state, items: [...state.items, item] });
      return () => {
        const current = store.get(agentId);
        if (!current) return;
        store.set(agentId, { ...current, items: current.items.filter((i) => i.id !== id) });
      };
    },
    dispose() {
      for (const stream of streams.values()) {
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
