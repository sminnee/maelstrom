import type { Filters } from '../selectors/filters';
import { noFilters } from '../selectors/filters';
import type { ListFilters } from '../selectors/taskList';
import { noListFilters } from '../selectors/taskList';
import type { WorktreeFilters } from '../selectors/worktrees';
import { noWorktreeFilters } from '../selectors/worktrees';
import type { AgentId, DocumentId, TaskId, WorktreeId } from '../protocol/ids';
import type { Zone } from '../protocol/progress';

/**
 * One tab in the panel: a session, a document, a worktree's changes, or one of its running web
 * services. A task expands on the canvas instead.
 */
export type PanelTab =
  | { key: string; kind: 'session'; agentId: AgentId }
  | { key: string; kind: 'document'; documentId: DocumentId }
  | { key: string; kind: 'changes'; worktreeId: WorktreeId }
  | { key: string; kind: 'devenv'; worktreeId: WorktreeId; service: string };

/** One item of the top bar's menu: a main view, or the panel, labelled `Tabs`. */
export type Pane = 'canvas' | 'list' | 'worktrees' | 'tabs';

/** One of the two slots the body draws a pane in. */
export type Side = 'left' | 'right';

/**
 * A main view: the desk, every task, or every worktree.
 *
 * Only `originsOf` in `selectors/filterOptions.ts` switches on this
 * exhaustively. Every other site must be edited by hand when it widens:
 * AppShell, MobileShell, FilterBar, and the branch-naming views in
 * `filterOptions`.
 */
export type View = Exclude<Pane, 'tabs'>;

/** A free agent in one worktree: what a **Worktree card** starts. */
export interface NewWorkSeed {
  kind: 'agent';
  project: string;
  branch: string;
}

export interface UiState {
  /** The side each pane shows on. Shift-click on a top bar item moves it. */
  anchors: Record<Pane, Side>;
  /** The pane in each slot. `null` is a closed slot. See `selectors/slots.ts`. */
  slots: Record<Side, Pane | null>;
  /** Panes, most recently selected first. The front one is always showing. */
  paneRecency: Pane[];
  filters: Filters;
  /** The task list's own filters. */
  listFilters: ListFilters;
  /** The worktree table's own filters. */
  worktreeFilters: WorktreeFilters;
  /** The open tabs. The active one is the location's `panel`: see `nav/location.ts`. */
  tabs: PanelTab[];
  /** Tab keys, most recently activated first. */
  tabRecency: string[];
  /**
   * Each worktree group's split tab, by group key: the tab the body shows in its right half,
   * beside the active tab. Never the active tab itself. See CONTEXT.md, "Split tab".
   */
  splitTabs: Record<string, string>;
  /** The task the editor is open on. */
  editingTaskId: TaskId | null;
  /**
   * Whether the new-work form is open. Only the flag lives here: the draft
   * itself is component state, as the editor's is, so a keystroke does not
   * publish to every subscriber of the store. It is also held text — closing
   * the form does not lose it. See `ui/useRetained.ts`.
   */
  newWorkOpen: boolean;
  /**
   * What the form opens on, when a surface opened it for one piece of work.
   * The form lays it over its held draft, and keeps the rest of the draft.
   */
  newWorkSeed: NewWorkSeed | null;
  /**
   * The right slot's width in px while both slots are open. Set by a drag; not
   * persisted across a reload.
   */
  panelWidth: number;
  /**
   * Which zone the deck list is showing. Narrow layout only: the canvas draws
   * every zone at once, so it has no such choice to make.
   */
  deckZone: Zone;
}

/**
 * How wide the panel opens, given the window.
 *
 * Half the window, because the canvas and the panel are both being read; and
 * never past 980px, because a transcript is prose and prose stops getting
 * easier to read once the line runs long. The shell clamps this again against its
 * own floor, so a window too narrow to halve still leaves the grip reachable.
 */
const openingWidth = () =>
  Math.min(980, (typeof window === 'undefined' ? 1440 : window.innerWidth) / 2);

export function initialUiState(): UiState {
  return {
    anchors: { canvas: 'left', list: 'left', worktrees: 'left', tabs: 'right' },
    slots: { left: 'canvas', right: 'tabs' },
    // Every pane, so the left slot can reopen on one that was never selected.
    paneRecency: ['canvas', 'tabs', 'list', 'worktrees'],
    filters: noFilters(),
    listFilters: noListFilters(),
    worktreeFilters: noWorktreeFilters(),
    tabs: [],
    tabRecency: [],
    splitTabs: {},
    editingTaskId: null,
    newWorkOpen: false,
    newWorkSeed: null,
    panelWidth: openingWidth(),
    // Running is where the work the user can act on is.
    deckZone: 'running',
  };
}
