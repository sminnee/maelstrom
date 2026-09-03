import type { Filters, GroupBy } from '../selectors/filters';
import { noFilters } from '../selectors/filters';
import type { ListFilters } from '../selectors/taskList';
import { noListFilters } from '../selectors/taskList';
import type { AgentId, DocumentId, TaskId } from '../protocol/ids';

/** One tab in the right-hand panel: a session or a document. A task expands on the canvas instead. */
export type PanelTab =
  | { key: string; kind: 'session'; agentId: AgentId }
  | { key: string; kind: 'document'; documentId: DocumentId };

/** Which of the two main views is showing: the desk, or every task. */
export type View = 'canvas' | 'list';

export interface UiState {
  view: View;
  groupBy: GroupBy;
  filters: Filters;
  /** The task list's own filters. */
  listFilters: ListFilters;
  tabs: PanelTab[];
  activeTabKey: string | null;
  /** The one node grown into a card on the canvas, if any. */
  expandedTaskId: TaskId | null;
  drawerOpen: boolean;
  panelWidth: number;
}

export function initialUiState(): UiState {
  return {
    view: 'canvas',
    groupBy: 'project',
    filters: noFilters(),
    listFilters: noListFilters(),
    tabs: [],
    activeTabKey: null,
    expandedTaskId: null,
    drawerOpen: false,
    panelWidth: 460,
  };
}
