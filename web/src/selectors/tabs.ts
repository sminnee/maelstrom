import type { PanelTab } from '../store/uiSlice';

/** Add `tab` unless a tab with its key is open already. Either way it is the one to focus. */
export function openOrFocusTab(tabs: PanelTab[], tab: PanelTab): PanelTab[] {
  return tabs.some((t) => t.key === tab.key) ? tabs : [...tabs, tab];
}

/** Remove the tab; if it was active, its right neighbour (else left) takes over. */
export function closeTab(
  tabs: PanelTab[],
  activeTabKey: string | null,
  key: string,
): { tabs: PanelTab[]; activeTabKey: string | null } {
  const index = tabs.findIndex((t) => t.key === key);
  if (index === -1) return { tabs, activeTabKey };
  const remaining = tabs.filter((t) => t.key !== key);
  if (activeTabKey !== key) return { tabs: remaining, activeTabKey };
  const neighbour = remaining[index] ?? remaining[index - 1] ?? null;
  return { tabs: remaining, activeTabKey: neighbour?.key ?? null };
}

export const summaryTab = (agentId: string): PanelTab => ({
  key: `summary:${agentId}`,
  kind: 'summary',
  agentId,
});
export const sessionTab = (agentId: string): PanelTab => ({
  key: `session:${agentId}`,
  kind: 'session',
  agentId,
});
export const documentTab = (documentId: string): PanelTab => ({
  key: `document:${documentId}`,
  kind: 'document',
  documentId,
});
