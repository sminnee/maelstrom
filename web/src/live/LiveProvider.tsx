import { useEffect, useMemo, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAppStore } from '../store/store';
import { createAgentStreams } from './agentStreams';
import { startChangeStream, type EventSourceLike } from './changeStream';
import type { SocketLike } from './socketLike';
import { AgentStreamsContext } from './useAgentStream';

/**
 * Keeps the query cache fresh from the server's change stream for as long as
 * it is mounted, puts the connection state in the store, and owns the
 * per-agent transcript streams the views acquire. Started in effects, so each
 * test render gets streams of its own.
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
  const streams = useMemo(
    () =>
      createAgentStreams({
        socketFactory,
        reconnectMs,
        store: {
          get: (agentId) => useAppStore.getState().transcripts[agentId],
          set: (agentId, state) => useAppStore.getState().setTranscript(agentId, state),
          drop: (agentId) => useAppStore.getState().dropTranscript(agentId),
        },
      }),
    [socketFactory, reconnectMs],
  );
  useEffect(() => () => streams.stop(), [streams]);
  return <AgentStreamsContext.Provider value={streams}>{children}</AgentStreamsContext.Provider>;
}
