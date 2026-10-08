import { create } from 'zustand';
import type { ConnectionState } from '../live/changeStream';
import type { TranscriptState } from '../live/transcriptReducer';
import type { AgentId, TaskId } from '../protocol/ids';
import type { Filters } from '../selectors/filters';
import type { ListFilters } from '../selectors/taskList';
import type { WorktreeFilters } from '../selectors/worktrees';
import type { NewWorkSeed, Pane, PanelTab, UiState } from './uiSlice';
import { initialUiState } from './uiSlice';
import {
  activateTab as activateTabIn,
  closeTabs as closeTabsIn,
  openTab as openTabIn,
  selectGroup as selectGroupIn,
  toggleSplit as toggleSplitIn,
} from '../selectors/tabs';
import type { MobileScreen } from '../selectors/navStack';
import { popScreen, pushScreen } from '../selectors/navStack';
import {
  moveAnchor as moveAnchorIn,
  showPane as showPaneIn,
  togglePane as togglePaneIn,
} from '../selectors/slots';
import type { Zone } from '../protocol/progress';

export interface AppStore {
  ui: UiState;
  /** What the change stream is doing. */
  connection: ConnectionState;
  /** The transcripts some view is showing, kept live by their streams. */
  transcripts: Record<AgentId, TranscriptState>;
  setConnection(state: ConnectionState): void;
  setTranscript(agentId: AgentId, state: TranscriptState): void;
  dropTranscript(agentId: AgentId): void;
  reset(): void;
  /** Show a pane in the slot of its anchor. */
  showPane(pane: Pane): void;
  /** Show a pane, or close the slot of one that is showing. Wide layout only. */
  togglePane(pane: Pane): void;
  /** Move a pane's anchor to the other side. Wide layout only. */
  moveAnchor(pane: Pane): void;
  setFilters(patch: Partial<Filters>): void;
  setListFilters(patch: Partial<ListFilters>): void;
  setWorktreeFilters(patch: Partial<WorktreeFilters>): void;
  /** Open a tab, or focus it. A split tab opened again leaves the split and fills the body. */
  openTab(tab: PanelTab): void;
  /** Make a tab of the strip active. A click on the split tab does nothing: it is already showing. */
  activateTab(key: string): void;
  /** Show a tab in the right half of its group's body, or take it out. `groupOf` as for `closeTabs`. */
  toggleSplit(key: string, groupOf: (tab: PanelTab) => string): void;
  /**
   * Close tabs. `groupOf` names each tab's worktree group, which the store
   * cannot work out: the world lives in the query cache, not here.
   */
  closeTabs(keys: string[], groupOf: (tab: PanelTab) => string): void;
  /** Show a worktree group: activate the most recent of its tabs, past its split tab. */
  selectGroup(tabKeys: string[]): void;
  /** Open the editor on a task, or close it with `null`. */
  setEditingTask(taskId: TaskId | null): void;
  /** Open or close the new-work form. A `seed` is what it opens on. */
  setNewWorkOpen(open: boolean, seed?: NewWorkSeed): void;
  /** Drop the seed once the form has taken it, so a remount does not lay it again. */
  clearNewWorkSeed(): void;
  setPanelWidth(width: number): void;
  /** Which zone the deck list shows. Narrow layout only. */
  setDeckZone(zone: Zone): void;
  /** Push a screen over the deck list, or return to it if it is already open. */
  pushScreen(screen: MobileScreen): void;
  /** Go back one screen. At the deck list this does nothing. */
  popScreen(): void;
  /** Drop every pushed screen and return to the deck list. */
  clearStack(): void;
}

/**
 * One store for what is not fetched: UI state, the connection state and the
 * open transcripts. The world itself lives in the query cache.
 */
export const useAppStore = create<AppStore>()((set) => ({
  ui: initialUiState(),
  connection: 'connecting',
  transcripts: {},
  setConnection: (connection) => set({ connection }),
  setTranscript: (agentId, state) =>
    set((s) => ({ transcripts: { ...s.transcripts, [agentId]: state } })),
  dropTranscript: (agentId) =>
    set((s) => {
      if (!(agentId in s.transcripts)) return s;
      const transcripts = { ...s.transcripts };
      delete transcripts[agentId];
      return { transcripts };
    }),
  reset: () => set({ ui: initialUiState(), transcripts: {}, connection: 'connecting' }),
  showPane: (pane) => set((s) => ({ ui: { ...s.ui, ...showPaneIn(s.ui, pane) } })),
  togglePane: (pane) => set((s) => ({ ui: { ...s.ui, ...togglePaneIn(s.ui, pane) } })),
  moveAnchor: (pane) => set((s) => ({ ui: { ...s.ui, ...moveAnchorIn(s.ui, pane) } })),
  setFilters: (patch) => set((s) => ({ ui: { ...s.ui, filters: { ...s.ui.filters, ...patch } } })),
  setListFilters: (patch) =>
    set((s) => ({ ui: { ...s.ui, listFilters: { ...s.ui.listFilters, ...patch } } })),
  setWorktreeFilters: (patch) =>
    set((s) => ({ ui: { ...s.ui, worktreeFilters: { ...s.ui.worktreeFilters, ...patch } } })),
  // Opening a tab always shows the panel: a link must show what it opened.
  openTab: (tab) =>
    set((s) => {
      const ui = { ...s.ui, ...openTabIn(s.ui, tab) };
      return { ui: { ...ui, ...showPaneIn(ui, 'tabs') } };
    }),
  activateTab: (key) => set((s) => ({ ui: { ...s.ui, ...activateTabIn(s.ui, key) } })),
  toggleSplit: (key, groupOf) =>
    set((s) => ({ ui: { ...s.ui, ...toggleSplitIn(s.ui, key, groupOf) } })),
  closeTabs: (keys, groupOf) =>
    set((s) => ({ ui: { ...s.ui, ...closeTabsIn(s.ui, keys, groupOf) } })),
  selectGroup: (tabKeys) => set((s) => ({ ui: { ...s.ui, ...selectGroupIn(s.ui, tabKeys) } })),
  setEditingTask: (editingTaskId) => set((s) => ({ ui: { ...s.ui, editingTaskId } })),
  setNewWorkOpen: (newWorkOpen, seed) =>
    set((s) => ({ ui: { ...s.ui, newWorkOpen, newWorkSeed: (newWorkOpen && seed) || null } })),
  clearNewWorkSeed: () =>
    set((s) => (s.ui.newWorkSeed ? { ui: { ...s.ui, newWorkSeed: null } } : s)),
  setPanelWidth: (panelWidth) => set((s) => ({ ui: { ...s.ui, panelWidth } })),
  setDeckZone: (deckZone) => set((s) => ({ ui: { ...s.ui, deckZone } })),
  pushScreen: (screen) =>
    set((s) => ({ ui: { ...s.ui, mobileStack: pushScreen(s.ui.mobileStack, screen) } })),
  popScreen: () =>
    set((s) =>
      s.ui.mobileStack.length === 0
        ? s
        : { ui: { ...s.ui, mobileStack: popScreen(s.ui.mobileStack) } },
    ),
  clearStack: () =>
    set((s) => (s.ui.mobileStack.length === 0 ? s : { ui: { ...s.ui, mobileStack: [] } })),
}));
