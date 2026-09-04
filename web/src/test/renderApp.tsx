import { fireEvent, render, type RenderResult } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { act } from 'react';
import { App } from '../App';
import { createFakeBackend } from '../fake-backend/createFakeBackend';
import type { DebugBackend } from '../protocol/backend';
import { createFakeServer, type FakeServer } from './fakeServer';

/**
 * Mount the app on a paused fake backend, with a fake server behind the API
 * and the change stream. Advance the world with `backend.sim.step()`.
 */
export async function renderApp(
  opts: { seed?: number } = {},
): Promise<RenderResult & { backend: DebugBackend; server: FakeServer }> {
  const backend: DebugBackend = createFakeBackend({ seed: opts.seed ?? 1, autoplay: false });
  const server = createFakeServer();
  // No retries: a refused request must fail the test now, not after backoff.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: 0 } },
  });
  const deps = { api: server.api, eventSourceFactory: server.eventSourceFactory, queryClient };
  const utils = render(<App backend={backend} deps={deps} />);
  await act(async () => {
    await backend.connect();
  });
  return { backend, server, ...utils };
}

/** Drop the fake change stream, as the browser reports a drop, and let the app react. */
export async function dropStream(server: FakeServer, how: 'connecting' | 'closed' = 'connecting') {
  await act(async () => {
    server.dropStream(how);
  });
}

/** Run `n` simulation ticks inside React's act so the resulting renders flush. */
export async function stepSim(backend: DebugBackend, n = 1) {
  await act(async () => {
    backend.sim.step(n);
  });
}

/**
 * Click a canvas node. A plain click event, not a user-event pointer sequence:
 * d3-zoom under React Flow reads `event.view.document` on mousedown, and
 * jsdom mouse events from user-event carry a null view.
 */
export function clickNode(taskId: string): Element {
  const node = document.querySelector(`[data-task-id="${taskId}"]`);
  if (!node) throw new Error(`No node for ${taskId}`);
  fireEvent.click(node);
  return node;
}

/** Press one key on whatever has focus, else the body. */
export function pressKey(key: string) {
  fireEvent.keyDown(document.activeElement ?? document.body, { key });
}

/**
 * Select `text[start, end)` inside a text node, as a drag would. jsdom queues
 * selectionchange; the helper fires it at once so the test needs no wait.
 */
export function selectText(node: Node, start: number, end: number) {
  const range = document.createRange();
  range.setStart(node, start);
  range.setEnd(node, end);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  fireEvent(document, new Event('selectionchange'));
}
