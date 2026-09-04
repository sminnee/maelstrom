import { useEffect, type ReactNode } from 'react';
import type { Backend } from '../protocol/backend';
import { useAppStore } from './store';

import { BackendContext } from './backendContext';

/** The fake backend, for the controls that drive it. Goes with the fake. */
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
    // A rejected connect is a closed socket, which the backend reconnects
    // from or the user closed on purpose; nothing here needs to hear it.
    if (autoConnect) backend.connect().catch(() => undefined);
    return () => {
      if (autoConnect) backend.close();
    };
  }, [backend, autoConnect]);
  return <BackendContext.Provider value={backend}>{children}</BackendContext.Provider>;
}
