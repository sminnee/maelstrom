import type { DebugBackend, SimControls } from '../protocol/backend';
import type { Command, CommandError, Reply, ResultFor } from '../protocol/commands';
import type { EventFrame, ServerEvent } from '../protocol/events';
import type { Seq } from '../protocol/ids';
import type { ClientState } from '../protocol/reducer';
import { validateCommand } from '../protocol/validate';
import type { Store } from './store';

export interface Clock {
  now(): string;
}

export interface BackendDeps {
  store: Store;
  clock: Clock;
  sim: SimControls;
  /** Run a validated command: the events to append and the ack payload, or a refusal. */
  runCommand(
    state: ClientState,
    cmd: Command,
    now: string,
  ): { events: ServerEvent[]; result: unknown } | { error: CommandError };
}

export interface InMemoryBackend extends DebugBackend {
  /** Append events (from the simulation) and deliver them to subscribers. */
  publish(events: ServerEvent[]): void;
}

/**
 * The fake transport. `connect` delivers a snapshot or a replay through
 * `subscribe`, `command` validates then applies its consequence as events,
 * and the simulation publishes its own events between commands.
 */
export function createInMemoryBackend(deps: BackendDeps): InMemoryBackend {
  const { store, clock } = deps;
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
    sim: deps.sim,
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
      const out = deps.runCommand(store.state, cmd, clock.now());
      if ('error' in out) return { ok: false, error: out.error };
      emit(store.append(out.events, clock.now()));
      return { ok: true, result: out.result as ResultFor<C> };
    },
    publish(events) {
      emit(store.append(events, clock.now()));
    },
    close() {
      // Subscriptions survive a close, as they survive a dropped WebSocket:
      // the next connect delivers to them again.
      connected = false;
    },
  };
}
