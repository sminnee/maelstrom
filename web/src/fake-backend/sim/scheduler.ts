import type { SimState } from '../../protocol/backend';

/**
 * The only impure part of the simulation: a timer that calls `onTick`
 * `speed` times per `baseMs`. Play, pause, step and speed live here.
 */
export function createScheduler(opts: { baseMs: number; onTick: () => void }) {
  const state: SimState = { playing: false, speed: 1, tick: 0 };
  let timer: ReturnType<typeof setInterval> | null = null;
  const listeners = new Set<() => void>();
  const changed = () => {
    for (const l of [...listeners]) l();
  };

  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
  };
  const start = () => {
    stop();
    timer = setInterval(() => step(1), opts.baseMs / state.speed);
  };
  const step = (n = 1) => {
    for (let i = 0; i < n; i += 1) {
      state.tick += 1;
      opts.onTick();
    }
    changed();
  };

  return {
    state,
    play() {
      state.playing = true;
      start();
      changed();
    },
    pause() {
      state.playing = false;
      stop();
      changed();
    },
    step,
    setSpeed(x: number) {
      state.speed = Math.max(0.25, Math.min(16, x));
      if (state.playing) start();
      changed();
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
