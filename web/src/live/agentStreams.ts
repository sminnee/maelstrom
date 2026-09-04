import type { AgentId } from '../protocol/ids';
import type { TranscriptItem } from '../protocol/transcript';
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
  /** Close every stream now. */
  stop(): void;
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
  let stopped = false;

  const update = (agentId: AgentId, patch: Partial<TranscriptState>) => {
    store.set(agentId, { ...(store.get(agentId) ?? emptyTranscript()), ...patch });
  };

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
        store.set(agentId, { ...reduceTranscript(current, message), status: 'live' });
        return;
      }
      if (message.type === 'transcript.snapshot') {
        store.set(agentId, {
          items: message.items,
          truncatedBefore: message.truncatedBefore,
          cursor: message.seq,
          status: 'live',
        });
      } else if (message.type === 'transcript.replay') {
        let state = store.get(agentId) ?? emptyTranscript();
        for (const frame of message.frames) state = reduceTranscript(state, frame);
        store.set(agentId, { ...state, cursor: message.seq, status: 'live' });
      }
    };
    socket.onclose = (event) => {
      if (stream.socket !== socket) return;
      stream.socket = null;
      if (stream.refs === 0 || stream.ended || stopped) return;
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
      if (stopped) return () => undefined;
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
        if (released || stopped) return;
        released = true;
        stream.refs -= 1;
        if (stream.refs > 0) return;
        stream.graceTimer = setTimeout(() => {
          stream.graceTimer = null;
          if (stream.refs === 0) close(agentId, stream);
        }, graceMs);
      };
    },
    stop() {
      stopped = true;
      for (const [agentId, stream] of [...streams]) close(agentId, stream);
    },
  };
}
