import type { DebugBackend, ForcedBeat, SimControls } from '../protocol/backend';
import { createInMemoryBackend, type InMemoryBackend } from './inMemoryBackend';
import { SEED_TIME, seedWorld } from './scenarios/seedWorld';
import { CommandRefused, applyCommand } from './sim/commands';
import { mulberry32 } from './sim/rng';
import { createScheduler } from './sim/scheduler';
import { initialSimState, step, type SimWorld } from './sim/stepper';
import { createStore } from './store';

export interface FakeBackendOptions {
  /** Drives every random choice; the same seed yields the same events. */
  seed?: number;
  /** Start the simulation clock on connect. Tests use `false` and `sim.step()`. */
  autoplay?: boolean;
  /** Wall-clock ms per tick at speed 1. */
  tickMs?: number;
}

/** How much simulated time one tick covers. */
const SIM_SECONDS_PER_TICK = 20;

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

  const rng = mulberry32(opts.seed ?? 1);
  let sim: SimWorld = initialSimState(store.state);

  const scheduler = createScheduler({
    baseMs: opts.tickMs ?? 1200,
    onTick: () => {
      nowMs += SIM_SECONDS_PER_TICK * 1000;
      const out = step(store.state, sim, rng, clock.now());
      sim = out.sim;
      backend.publish(out.events);
    },
  });

  const controls: SimControls = {
    play: scheduler.play,
    pause: scheduler.pause,
    step: scheduler.step,
    setSpeed: scheduler.setSpeed,
    subscribe: scheduler.subscribe,
    force: (f: ForcedBeat) => {
      sim = { ...sim, force: [...sim.force, f] };
    },
    get state() {
      return scheduler.state;
    },
  };

  const backend: InMemoryBackend = createInMemoryBackend({
    store,
    clock,
    sim: controls,
    runCommand: (state, cmd, now) => {
      try {
        const out = applyCommand(state, sim, cmd, now);
        sim = out.sim;
        return { events: out.events, result: out.result };
      } catch (e) {
        if (e instanceof CommandRefused) return { error: e.error };
        throw e;
      }
    },
  });

  const connect = backend.connect.bind(backend);
  const close = backend.close.bind(backend);
  return {
    ...backend,
    async connect(o) {
      await connect(o);
      if (opts.autoplay) controls.play();
    },
    close() {
      controls.pause();
      close();
    },
  };
}
