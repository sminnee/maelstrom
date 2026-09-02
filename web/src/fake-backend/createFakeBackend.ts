import type { DebugBackend, SimControls, SimState } from '../protocol/backend';
import { createInMemoryBackend } from './inMemoryBackend';
import { SEED_TIME, seedWorld } from './scenarios/seedWorld';
import { createStore } from './store';

export interface FakeBackendOptions {
  /** Drives every random choice; the same seed yields the same events. */
  seed?: number;
  /** Start the simulation clock on connect. Tests use `false` and `sim.step()`. */
  autoplay?: boolean;
}

/** A `DebugBackend` over an in-browser simulation. */
export function createFakeBackend(opts: FakeBackendOptions = {}): DebugBackend {
  const store = createStore();
  const seed = seedWorld();
  let nowMs = Date.parse(SEED_TIME);
  const clock = { now: () => new Date(nowMs).toISOString() };
  store.append(
    [{ type: 'snapshot', world: seed.world, transcripts: seed.transcripts }],
    clock.now(),
  );

  const state: SimState = { playing: false, speed: 1, tick: 0 };
  const sim: SimControls = {
    play: () => {
      state.playing = true;
    },
    pause: () => {
      state.playing = false;
    },
    step: (n = 1) => {
      state.tick += n;
      nowMs += n * 1000;
    },
    setSpeed: (x) => {
      state.speed = x;
    },
    force: () => {},
    get state() {
      return state;
    },
  };
  void opts;
  return createInMemoryBackend(store, clock, sim);
}
