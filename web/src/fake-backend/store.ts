import type { ClientState } from '../protocol/reducer';
import { applyEvent, initialClientState } from '../protocol/reducer';
import type { EventFrame, ServerEvent } from '../protocol/events';
import type { Seq } from '../protocol/ids';

/** How many frames the log keeps for `replayFrom`. Older resumes get a snapshot. */
export const RING_SIZE = 2000;

/**
 * The fake backend's only state: the world, reached by applying events
 * through the same reducer the client uses, and the seq-stamped log of those
 * events. Nothing mutates the world directly.
 */
export interface Store {
  readonly state: ClientState;
  readonly seq: Seq;
  /** Apply `events` in order, stamp them, append to the log, return the frames. */
  append(events: ServerEvent[], ts: string): EventFrame[];
  /** Frames with seq > `from`, or null when `from` is older than the log holds. */
  replayFrom(from: Seq): EventFrame[] | null;
  /** A snapshot of the world as it is now, stamped with the current seq. */
  snapshotFrame(ts: string): EventFrame;
}

export function createStore(): Store {
  let state = initialClientState();
  let seq: Seq = 0;
  const log: EventFrame[] = [];

  return {
    get state() {
      return state;
    },
    get seq() {
      return seq;
    },
    append(events, ts) {
      const frames: EventFrame[] = [];
      for (const event of events) {
        seq += 1;
        state = applyEvent(state, event, seq);
        const frame = { seq, ts, event };
        log.push(frame);
        frames.push(frame);
      }
      if (log.length > RING_SIZE) log.splice(0, log.length - RING_SIZE);
      return frames;
    },
    replayFrom(from) {
      // A resume must start at or after the first frame still held: `from` is
      // the last seq the client applied, so the ring must hold `from + 1`.
      const oldest = log[0];
      if (!oldest || from < oldest.seq - 1) return null;
      return log.filter((f) => f.seq > from);
    },
    snapshotFrame(ts) {
      return {
        seq,
        ts,
        event: { type: 'snapshot', world: state.world, transcripts: state.transcripts },
      };
    },
  };
}
