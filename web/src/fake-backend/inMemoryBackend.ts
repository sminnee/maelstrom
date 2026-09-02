import type { DebugBackend, SimControls } from '../protocol/backend';
import type { Command, Reply, ResultFor } from '../protocol/commands';
import type { EventFrame, ServerEvent } from '../protocol/events';
import type { Seq } from '../protocol/ids';
import { validateCommand } from '../protocol/validate';
import type { Store } from './store';
import { applyCommand } from './sim/commands';

export interface Clock {
  now(): string;
}

/**
 * The fake transport. `connect` delivers a snapshot or a replay through
 * `subscribe`, `command` validates then applies its consequence as events,
 * and everything else is the simulation's business (`sim`).
 */
export function createInMemoryBackend(store: Store, clock: Clock, sim: SimControls): DebugBackend {
  const listeners = new Set<(frame: EventFrame) => void>();
  let connected = false;

  function emit(frames: EventFrame[]) {
    // Frames produced while disconnected are still in the store's log, and
    // the next connect replays them from `resumeFrom`, so dropping them here
    // loses nothing.
    if (!connected) return;
    const current = [...listeners];
    for (const frame of frames) for (const l of current) l(frame);
  }

  return {
    sim,
    async connect(opts) {
      connected = true;
      const resumeFrom: Seq | undefined = opts?.resumeFrom;
      const replay = resumeFrom === undefined ? null : store.replayFrom(resumeFrom);
      emit(replay ?? [store.snapshotFrame(clock.now())]);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async command<C extends Command>(cmd: C): Promise<Reply<C>> {
      const error = validateCommand(store.state.world, cmd);
      if (error) return { ok: false, error };
      const { events, result } = applyCommand(store.state, cmd, clock.now());
      emit(store.append(events, clock.now()));
      return { ok: true, result: result as ResultFor<C> };
    },
    close() {
      // Subscriptions survive a close, as they survive a dropped WebSocket:
      // the next connect delivers to them again.
      connected = false;
    },
  };
}

export type { ServerEvent };
