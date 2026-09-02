import type { Backend } from './protocol/backend';
import { AppShell } from './shell/AppShell';
import { BackendProvider } from './store/BackendProvider';

export function App({ backend, autoConnect = false }: { backend: Backend; autoConnect?: boolean }) {
  return (
    <BackendProvider backend={backend} autoConnect={autoConnect}>
      <AppShell />
    </BackendProvider>
  );
}
