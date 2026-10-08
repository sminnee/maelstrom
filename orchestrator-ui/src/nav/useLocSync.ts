import { useEffect } from 'react';
import { useAppStore } from '../store/store';
import { useLoc } from './useNav';

/**
 * Keeps the workspace in step with the location. The URL says where the user is; the store
 * keeps what is laid out around it. A move to a view shows that view's pane, in the slot of
 * its anchor and at the front of the recency, which the medium layout draws.
 */
export function useLocSync() {
  const { view } = useLoc();
  const showPane = useAppStore((s) => s.showPane);
  useEffect(() => showPane(view), [view, showPane]);
}
