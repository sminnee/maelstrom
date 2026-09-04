import { useEffect, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAppStore } from '../store/store';
import { startChangeStream, type EventSourceLike } from './changeStream';

/**
 * Keeps the query cache fresh from the server's change stream for as long as
 * it is mounted, and puts the connection state in the store. Started in an
 * effect, so each test render gets a stream of its own.
 */
export function LiveProvider({
  url,
  eventSourceFactory,
  children,
}: {
  url: string;
  eventSourceFactory?: (url: string) => EventSourceLike;
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
  return children;
}
