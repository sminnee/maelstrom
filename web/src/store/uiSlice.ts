import type { Filters, GroupBy } from '../selectors/filters';
import { noFilters } from '../selectors/filters';
import type { AgentId, DocumentId } from '../protocol/ids';

export type PanelTab =
  | { key: string; kind: 'summary'; agentId: AgentId }
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
