import type { Backend } from '../protocol/backend';
import { useAppStore } from './store';

/**
 * Wire a backend into the store: every frame goes through the protocol
 * reducer. Returns the unsubscribe. `connect` is the caller's job so tests
 * can control when the snapshot lands.
 */
export function bridgeBackend(backend: Backend): () => void {
  return backend.subscribe((frame) => useAppStore.getState().applyFrame(frame));
}
