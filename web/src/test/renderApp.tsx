import { fireEvent, render, waitFor, type RenderResult } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { App } from '../App';
import { keys } from '../api/keys';
import { useAppStore } from '../store/store';
import { createFakeServer, type FakeServer } from './fakeServer';
import { seedWorld } from './seedWorld';

/** The seven list queries the world is read from. */
const LIST_KEYS = [
  keys.projects(),
  keys.worktrees(),
  keys.tasks.list(),
  keys.agents.list(),
  keys.attention(),
  keys.desk(),
  keys.documents.list(),
];

/**
 * Mount the app on a fake server holding the seed world, behind the API, the
 * change stream and the transcript sockets. Move the world with
 * `server.change`, `server.append` and `server.patch`.
 *
 * With `ready: false` the server holds every reply, so every list query is
 * still loading. The test releases when it is ready to see the world arrive.
 */
export async function renderApp(
  opts: { ready?: boolean } = {},
): Promise<RenderResult & { server: FakeServer; queryClient: QueryClient }> {
  // The store is a module singleton: a test must not inherit the view, the
  // filters or the tabs the one before it left.
  useAppStore.getState().reset();
  const seed = seedWorld();
  const server = createFakeServer({ world: seed.world, transcripts: seed.transcripts });
  // No retries: a refused request must fail the test now, not after backoff.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: 0 } },
  });
  const deps = {
    api: server.api,
    eventSourceFactory: server.eventSourceFactory,
    webSocketFactory: server.webSocketFactory,
    // A real backoff would cost every reconnect test a second of wall clock.
    streamReconnectMs: 10,
    queryClient,
  };
  if (opts.ready === false) server.hold();
  const utils = render(<App deps={deps} />);
  if (opts.ready === false) return { server, queryClient, ...utils };
  await waitFor(() => {
    for (const key of LIST_KEYS) {
      if (queryClient.getQueryState(key)?.status !== 'success') throw new Error('not loaded');
    }
  });
  return { server, queryClient, ...utils };
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
