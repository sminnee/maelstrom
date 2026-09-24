import { QueryClient } from '@tanstack/react-query';

/**
 * The cache the app reads the world from. Nothing goes stale on its own: the
 * change stream invalidates what changed, so no query ever polls, and its
 * `reset` after a reconnect is what refetches after a network drop. A retry
 * backs off; a mutation never retries, because a re-sent approve is answered
 * `stale_request`.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: Infinity,
        gcTime: 5 * 60 * 1000,
        retry: 2,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 30_000),
        refetchOnWindowFocus: false,
      },
      mutations: { retry: 0 },
    },
  });
}
