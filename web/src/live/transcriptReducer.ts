import type { AgentId, TranscriptItemId } from '../protocol/ids';
import type { TranscriptItem } from '../protocol/transcript';

/** What the transcript stream is doing. */
export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'ended';

export interface TranscriptState {
  items: TranscriptItem[];
  /** True when the agent host's event window dropped older items. */
  truncatedBefore: boolean;
  /** The seq of the last frame applied: what a reconnect resumes from. */
  cursor: number;
  status: StreamStatus;
}

export type TranscriptEvent =
  | { type: 'transcript.append'; agentId: AgentId; item: TranscriptItem }
  | {
      type: 'transcript.update';
      agentId: AgentId;
      itemId: TranscriptItemId;
      patch: Partial<TranscriptItem>;
    }
  | { type: 'transcript.truncated'; agentId: AgentId };

export interface TranscriptFrame {
  seq: number;
  event: TranscriptEvent;
}

export function emptyTranscript(status: StreamStatus = 'connecting'): TranscriptState {
  return { items: [], truncatedBefore: false, cursor: 0, status };
}

/** The transcript after one frame. Pure: the state is never mutated. */
export function reduceTranscript(state: TranscriptState, frame: TranscriptFrame): TranscriptState {
  const { event } = frame;
  switch (event.type) {
    case 'transcript.append':
      return { ...state, items: [...state.items, event.item], cursor: frame.seq };
    case 'transcript.update':
      return {
        ...state,
        items: state.items.map((item) =>
          item.id === event.itemId ? ({ ...item, ...event.patch } as TranscriptItem) : item,
        ),
        cursor: frame.seq,
      };
    case 'transcript.truncated':
      return { ...state, truncatedBefore: true, cursor: frame.seq };
  }
}
