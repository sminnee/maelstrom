import type { Filters, GroupBy } from '../selectors/filters';
import { noFilters } from '../selectors/filters';
import type { ListFilters } from '../selectors/taskList';
import { noListFilters } from '../selectors/taskList';
import type { AgentId, DocumentId, TaskId } from '../protocol/ids';
import type { Zone } from '../protocol/progress';
import type { MobileScreen } from '../selectors/navStack';

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
  /** The one node grown into a card on the canvas, if any: a task or an agent. */
  expandedNodeId: string | null;
  /** The task the editor is open on. */
  editingTaskId: TaskId | null;
  /**
   * Whether the new-work form is open. Only the flag lives here: the draft
   * itself is component state, as the editor's is, so a keystroke does not
   * publish to every subscriber of the store.
   */
  newWorkOpen: boolean;
  panelWidth: number;
  /**
   * Which zone the deck list is showing. Narrow layout only: the canvas draws
   * every zone at once, so it has no such choice to make.
   */
  deckZone: Zone;
  /**
   * What the narrow layout has pushed over the deck list, deepest last. Empty
   * is the deck itself.
   */
  mobileStack: MobileScreen[];
}

export function initialUiState(): UiState {
  return {
    view: 'canvas',
    groupBy: 'project',
    filters: noFilters(),
    listFilters: noListFilters(),
    tabs: [],
    activeTabKey: null,
    expandedNodeId: null,
    editingTaskId: null,
    newWorkOpen: false,
    panelWidth: 460,
    // Running is where the work the user can act on is.
    deckZone: 'running',
    mobileStack: [],
  };
}
