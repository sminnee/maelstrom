import type { EventFrame, ServerEvent, World } from './events';
import { ENTITY_KINDS, WORLD_KEY } from './events';
import type { AgentId, Seq } from './ids';
import type { Transcript, TranscriptItem } from './transcript';

export interface ClientState {
  world: World;
  transcripts: Record<AgentId, Transcript>;
  lastSeq: Seq;
  /** Errors the server sent, most recent last. */
  errors: { seq: Seq; message: string; agentId?: AgentId }[];
}

export function emptyWorld(): World {
  return {
    projects: {},
    worktrees: {},
    tasks: {},
    agents: {},
    documents: {},
    comments: {},
    attention: {},
    desk: {},
  };
}

export function initialClientState(): ClientState {
  return { world: emptyWorld(), transcripts: {}, lastSeq: 0, errors: [] };
}

/**
 * The client state after one frame. Pure. A frame whose seq is not newer than
 * the last one applied is dropped, which is what makes replay idempotent.
 * A snapshot is the exception — see "The snapshot epoch rule" in
 * `docs/dev/orchestrator-server.md`.
 */
export function applyServerEvent(state: ClientState, frame: EventFrame): ClientState {
  if (frame.event.type !== 'snapshot' && frame.seq <= state.lastSeq) return state;
  const next = applyEvent(state, frame.event, frame.seq);
  return { ...next, lastSeq: frame.seq };
}

/**
 * The same reduction without the seq guard, for a producer that has not
 * stamped its events yet. The fake backend's store uses it.
 *
 * A malformed event throws on purpose: it is a protocol bug, not a runtime
 * condition, and a client that carried on would be showing a world it can no
 * longer trust.
 */
export function applyEvent(state: ClientState, event: ServerEvent, seq: Seq = 0): ClientState {
  switch (event.type) {
    case 'snapshot':
      // A snapshot without transcripts leaves the ones this client holds. The
      // real server sends none: it relays transcript events rather than
      // storing them, so the client's own map is the only copy.
      return { ...state, world: event.world, transcripts: event.transcripts ?? state.transcripts };
    case 'upsert': {
      assertKnownKind(event.kind);
      const key = WORLD_KEY[event.kind];
      const table = { ...state.world[key], [event.entity.id]: event.entity };
      return { ...state, world: { ...state.world, [key]: table } };
    }
    case 'remove': {
      assertKnownKind(event.kind);
      const key = WORLD_KEY[event.kind];
      const table = { ...state.world[key] } as Record<string, unknown>;
      delete table[event.id];
      return { ...state, world: { ...state.world, [key]: table } };
    }
    case 'transcript.append': {
      const current = state.transcripts[event.agentId] ?? {
        agentId: event.agentId,
        items: [],
        truncatedBefore: false,
      };
      const transcript = { ...current, items: [...current.items, event.item] };
      return { ...state, transcripts: { ...state.transcripts, [event.agentId]: transcript } };
    }
    case 'transcript.update': {
      const current = state.transcripts[event.agentId];
      if (!current) return state;
      const items = current.items.map((item) =>
        item.id === event.itemId ? ({ ...item, ...event.patch } as TranscriptItem) : item,
      );
      return {
        ...state,
        transcripts: { ...state.transcripts, [event.agentId]: { ...current, items } },
      };
    }
    case 'transcript.truncated': {
      const current = state.transcripts[event.agentId] ?? {
        agentId: event.agentId,
        items: [],
        truncatedBefore: false,
      };
      const transcript = { ...current, truncatedBefore: true };
      return { ...state, transcripts: { ...state.transcripts, [event.agentId]: transcript } };
    }
    case 'error':
      return {
        ...state,
        errors: [...state.errors, { seq, message: event.message, agentId: event.agentId }],
      };
    default:
      throw new Error(`Unknown server event: ${JSON.stringify(event)}`);
  }
}

function assertKnownKind(kind: string): void {
  if (!(ENTITY_KINDS as readonly string[]).includes(kind)) {
    throw new Error(`Unknown entity kind: ${kind}`);
  }
}
