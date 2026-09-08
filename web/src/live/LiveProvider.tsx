import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAppStore } from '../store/store';
import { createAgentStreams } from './agentStreams';
import { startChangeStream, type EventSourceLike } from './changeStream';
import type { SocketLike } from './socketLike';
import { AgentStreamsContext } from './useAgentStream';

/**
 * Keeps the query cache fresh from the server's change stream for as long as
 * it is mounted, puts the connection state in the store, and owns the
 * per-agent transcript streams the views acquire. The change stream starts in
 * an effect, so each test render gets one of its own; the streams are built
 * once and outlive a remount.
 */
export function LiveProvider({
  url,
  eventSourceFactory,
  socketFactory,
  reconnectMs,
  children,
}: {
  url: string;
  eventSourceFactory?: (url: string) => EventSourceLike;
  socketFactory?: (path: string) => SocketLike;
  reconnectMs?: number;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const cancelDispose = useRef<(() => void) | null>(null);
  const setConnection = useAppStore((s) => s.setConnection);
  useEffect(
    () =>
      startChangeStream({
        url,
        queryClient,
        onStatus: setConnection,
        eventSourceFactory,
      }),
    [url, queryClient, setConnection, eventSourceFactory],
  );
  // A release only drops a refcount: the stream stays open for `graceMs` so a
  // view that comes straight back keeps it, which is what carries a transcript
  // across a StrictMode remount. So the manager is not disposed on unmount --
  // that unmount is simulated, and closing there would drop the sockets the
  // remount reuses. It is disposed when the tree is really gone, below.
  const [streams] = useState(() =>
    createAgentStreams({
      socketFactory,
      reconnectMs,
      store: {
        get: (agentId) => useAppStore.getState().transcripts[agentId],
        set: (agentId, state) => useAppStore.getState().setTranscript(agentId, state),
        drop: (agentId) => useAppStore.getState().dropTranscript(agentId),
      },
    }),
  );
  // Disposing in the cleanup directly would break StrictMode: its remount runs
  // the cleanup and then re-runs the effect, so the sockets the remount means
  // to reuse would already be closed. Deferring by a tick tells the two apart
  // -- a remount re-runs the effect and cancels the dispose, a real unmount
  // never does.
  useEffect(() => {
    cancelDispose.current?.();
    cancelDispose.current = null;
    return () => {
      const timer = setTimeout(() => streams.dispose(), 0);
      cancelDispose.current = () => clearTimeout(timer);
    };
  }, [streams]);
  return <AgentStreamsContext.Provider value={streams}>{children}</AgentStreamsContext.Provider>;
}
