import { useEffect, type ReactNode } from 'react';
import type { Backend } from '../protocol/backend';
import { bridgeBackend } from './backendBridge';
import { useAppStore } from './store';

import { BackendContext } from './backendContext';

export function BackendProvider({
  backend,
  children,
  autoConnect,
}: {
  backend: Backend;
  children: ReactNode;
  /** Call `connect` on mount. Tests leave this off and connect themselves. */
  autoConnect: boolean;
}) {
  useEffect(() => {
    useAppStore.getState().reset();
    const unsubscribe = bridgeBackend(backend);
    if (autoConnect) void backend.connect();
    return unsubscribe;
  }, [backend, autoConnect]);
  return <BackendContext.Provider value={backend}>{children}</BackendContext.Provider>;
}
