import { useEffect, useState, type ReactNode } from 'react';
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
  // Each view releases its own stream on unmount, so the manager needs no
  // teardown of its own.
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
  return <AgentStreamsContext.Provider value={streams}>{children}</AgentStreamsContext.Provider>;
}
