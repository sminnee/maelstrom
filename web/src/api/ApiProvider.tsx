import { createContext, useContext, type ReactNode } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import type { ApiClient } from './http';

const ApiContext = createContext<ApiClient | null>(null);

/** The API client and the query cache every hook under it reads. */
export function ApiProvider({
  api,
  queryClient,
  children,
}: {
  api: ApiClient;
  queryClient: QueryClient;
  children: ReactNode;
}) {
  return (
    <ApiContext.Provider value={api}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ApiContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- the hook belongs with its provider
export function useApi(): ApiClient {
  const api = useContext(ApiContext);
  if (!api) throw new Error('useApi outside ApiProvider');
  return api;
}
