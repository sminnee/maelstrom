import type { QueryClient, QueryKey } from '@tanstack/react-query';
import { keys } from '../api/keys';
import type { ChangeNotice } from '../api/types';

/**
 * The part of an `EventSource` the stream uses, so a test can drive a fake
 * one. A real `EventSource` satisfies it as-is.
 */
export interface EventSourceLike {
  readonly readyState: number;
  onopen: (() => void) | null;
  onerror: (() => void) | null;
  addEventListener(type: string, listener: (event: { data: string }) => void): void;
  close(): void;
}

export const CONNECTING = 0;
export const OPEN = 1;
export const CLOSED = 2;

export type ConnectionState = 'connecting' | 'live' | 'reconnecting';

export interface ChangeStreamOptions {
  url: string;
  queryClient: QueryClient;
  onStatus: (state: ConnectionState) => void;
  eventSourceFactory?: (url: string) => EventSourceLike;
  /** How long notices wait for company before the cache is invalidated. */
  coalesceMs?: number;
  /** The first wait before re-creating a closed source; doubles per attempt. */
  reconnectMs?: number;
}

/** The longest wait between re-creations of a closed source. */
const MAX_RECONNECT_MS = 30_000;

/** The keys one change notice invalidates. An empty id list means the whole kind. */
export function invalidationsFor(notice: ChangeNotice): QueryKey[] {
  switch (notice.kind) {
    case 'project':
      return [keys.projects()];
    case 'worktree':
      return [keys.worktrees()];
    case 'desk':
      return [keys.desk()];
    case 'attention':
      return [keys.attention()];
    case 'task':
      return perId(keys.tasks, notice.ids);
    case 'agent':
      return perId(keys.agents, notice.ids);
    case 'document':
      return perId(keys.documents, notice.ids);
  }
}

function perId(
  resource: { all: () => QueryKey; list: () => QueryKey; detail: (id: string) => QueryKey },
  ids: string[],
): QueryKey[] {
  if (ids.length === 0) return [resource.all()];
  return [resource.list(), ...ids.map((id) => resource.detail(id))];
}

/**
 * Follow the server's change notices and invalidate what they name.
 *
 * Every connection opens with a `reset`, which invalidates everything: that
 * is the one answer to "what did I miss", on the first connect, after the
 * browser's own retry, and after a restart.
 *
 * The browser retries a dropped `EventSource` itself while `readyState` is
 * CONNECTING. Once it gives up (CLOSED), the stream makes a new one, with a
 * wait that doubles to 30 s. Returns the function that stops it all.
 */
export function startChangeStream(opts: ChangeStreamOptions): () => void {
  const factory =
    opts.eventSourceFactory ?? ((url) => new EventSource(url) as unknown as EventSourceLike);
  const coalesceMs = opts.coalesceMs ?? 150;
  const reconnectMs = opts.reconnectMs ?? 1000;
  const pending = new Map<string, QueryKey>();
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let attempts = 0;
  let source: EventSourceLike | null = null;
  let stopped = false;

  const flush = () => {
    flushTimer = null;
    const batch = [...pending.values()];
    pending.clear();
    for (const queryKey of batch) void opts.queryClient.invalidateQueries({ queryKey });
  };

  const queue = (notice: ChangeNotice) => {
    for (const queryKey of invalidationsFor(notice))
      pending.set(JSON.stringify(queryKey), queryKey);
    if (!flushTimer) flushTimer = setTimeout(flush, coalesceMs);
  };

  const connect = () => {
    if (stopped) return;
    const s = factory(opts.url);
    source = s;
    s.onopen = () => {
      attempts = 0;
      opts.onStatus('live');
    };
    s.addEventListener('reset', () => {
      void opts.queryClient.invalidateQueries();
    });
    s.addEventListener('change', (event) => {
      try {
        queue(JSON.parse(event.data) as ChangeNotice);
      } catch {
        // A notice that is not JSON is a server bug; the next reset covers it.
      }
    });
    s.onerror = () => {
      if (stopped) return;
      opts.onStatus('reconnecting');
      if (s.readyState !== CLOSED) return;
      s.close();
      const wait = Math.min(reconnectMs * 2 ** attempts, MAX_RECONNECT_MS);
      attempts += 1;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, wait);
    };
  };

  opts.onStatus('connecting');
  connect();

  return () => {
    stopped = true;
    if (flushTimer) clearTimeout(flushTimer);
    if (reconnectTimer) clearTimeout(reconnectTimer);
    source?.close();
  };
}
