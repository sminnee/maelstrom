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
    // A rejected connect is a closed socket, which the backend reconnects
    // from or the user closed on purpose; nothing here needs to hear it.
    if (autoConnect) backend.connect().catch(() => undefined);
    return () => {
      unsubscribe();
      // The store resets on the next mount, so the next connect must deliver
      // a fresh snapshot: closing is what makes a real backend send one.
      if (autoConnect) backend.close();
    };
  }, [backend, autoConnect]);
  return <BackendContext.Provider value={backend}>{children}</BackendContext.Provider>;
}
