import type { Filters, GroupBy } from '../selectors/filters';
import { noFilters } from '../selectors/filters';
import type { AgentId, DocumentId, TaskId } from '../protocol/ids';

/** One tab in the right-hand panel: a session or a document. A task expands on the canvas instead. */
export type PanelTab =
  | { key: string; kind: 'session'; agentId: AgentId }
  | { key: string; kind: 'document'; documentId: DocumentId };

export interface UiState {
  groupBy: GroupBy;
  filters: Filters;
  tabs: PanelTab[];
  activeTabKey: string | null;
  /** The one node grown into a card on the canvas, if any. */
  expandedTaskId: TaskId | null;
  drawerOpen: boolean;
  panelWidth: number;
}

export function initialUiState(): UiState {
  return {
    groupBy: 'project',
    filters: noFilters(),
    tabs: [],
    activeTabKey: null,
    expandedTaskId: null,
    drawerOpen: false,
    panelWidth: 460,
  };
}
