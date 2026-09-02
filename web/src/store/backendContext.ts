import { createContext, useContext } from 'react';
import type { Backend } from '../protocol/backend';

export const BackendContext = createContext<Backend | null>(null);

export function useBackend(): Backend {
  const backend = useContext(BackendContext);
  if (!backend) throw new Error('useBackend outside BackendProvider');
  return backend;
}
