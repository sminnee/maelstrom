import { render, type RenderResult } from '@testing-library/react';
import { act } from 'react';
import { App } from '../App';
import { createFakeBackend } from '../fake-backend/createFakeBackend';
import type { DebugBackend } from '../protocol/backend';

/** Mount the app on a paused fake backend. Advance the world with `backend.sim.step()`. */
export async function renderApp(
  opts: { seed?: number } = {},
): Promise<RenderResult & { backend: DebugBackend }> {
  const backend: DebugBackend = createFakeBackend({ seed: opts.seed ?? 1, autoplay: false });
  const utils = render(<App backend={backend} />);
  await act(async () => {
    await backend.connect();
  });
  return { backend, ...utils };
}

/** Run `n` simulation ticks inside React's act so the resulting renders flush. */
export async function stepSim(backend: DebugBackend, n = 1) {
  await act(async () => {
    backend.sim.step(n);
  });
}
