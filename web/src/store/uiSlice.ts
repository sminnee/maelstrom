import type { Filters, GroupBy } from '../selectors/filters';
import { noFilters } from '../selectors/filters';
import type { AgentId, DocumentId, TaskId } from '../protocol/ids';

/**
 * One tab in the right-hand panel. A summary is keyed by task: a task has at
 * most one live agent, and a queued task still has a summary (with Launch).
 */
export type PanelTab =
  | { key: string; kind: 'summary'; taskId: TaskId }
  | { key: string; kind: 'session'; agentId: AgentId }
  | { key: string; kind: 'document'; documentId: DocumentId };

export interface UiState {
  groupBy: GroupBy;
  filters: Filters;
  tabs: PanelTab[];
  activeTabKey: string | null;
  drawerOpen: boolean;
  panelWidth: number;
}

export function initialUiState(): UiState {
  return {
    groupBy: 'project',
    filters: noFilters(),
    tabs: [],
    activeTabKey: null,
    drawerOpen: false,
    panelWidth: 460,
  };
}
