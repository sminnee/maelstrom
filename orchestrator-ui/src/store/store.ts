import { create } from 'zustand';
import type { ConnectionState } from '../live/changeStream';
import type { TranscriptState } from '../live/transcriptReducer';
import type { AgentId, TaskId } from '../protocol/ids';
import type { Filters } from '../selectors/filters';
import type { ListFilters } from '../selectors/taskList';
import type { WorktreeFilters } from '../selectors/worktrees';
import type { Pane, PanelTab, UiState } from './uiSlice';
import { initialUiState } from './uiSlice';
import {
  closeTabs as closeTabsIn,
  mostRecentTab,
  openOrFocusTab,
  touchTab,
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
  openTab(tab: PanelTab): void;
  activateTab(key: string): void;
  /**
   * Close tabs. `groupOf` names each tab's worktree group, which the store
   * cannot work out: the world lives in the query cache, not here.
   */
  closeTabs(keys: string[], groupOf: (tab: PanelTab) => string): void;
  /** Show a worktree group: activate the most recent of its tabs. */
  selectGroup(tabKeys: string[]): void;
  /** Expand a node in place. With `toggle`, expanding the expanded node collapses it. */
  expandNode(taskId: TaskId, toggle?: boolean): void;
  collapseNode(): void;
  /** Open the editor on a task, or close it with `null`. */
  setEditingTask(taskId: TaskId | null): void;
  setNewWorkOpen(open: boolean): void;
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
  openTab: (tab) =>
    set((s) => {
      const tabs = openOrFocusTab(s.ui.tabs, tab);
      // Opening a tab always shows the panel: a link must show what it opened.
      return {
        ui: {
          ...s.ui,
          ...showPaneIn(s.ui, 'tabs'),
          tabs,
          activeTabKey: tab.key,
          tabRecency: touchTab(s.ui.tabRecency, tab.key),
        },
      };
    }),
  activateTab: (key) =>
    set((s) => ({
      ui: { ...s.ui, activeTabKey: key, tabRecency: touchTab(s.ui.tabRecency, key) },
    })),
  closeTabs: (keys, groupOf) =>
    set((s) => ({ ui: { ...s.ui, ...closeTabsIn(s.ui, keys, groupOf) } })),
  selectGroup: (tabKeys) =>
    set((s) => {
      const key = mostRecentTab(tabKeys, s.ui.tabRecency);
      return key
        ? { ui: { ...s.ui, activeTabKey: key, tabRecency: touchTab(s.ui.tabRecency, key) } }
        : s;
    }),
  expandNode: (nodeId, toggle = true) =>
    set((s) => ({
      ui: { ...s.ui, expandedNodeId: toggle && s.ui.expandedNodeId === nodeId ? null : nodeId },
    })),
  collapseNode: () =>
    set((s) => (s.ui.expandedNodeId ? { ui: { ...s.ui, expandedNodeId: null } } : s)),
  setEditingTask: (editingTaskId) => set((s) => ({ ui: { ...s.ui, editingTaskId } })),
  setNewWorkOpen: (newWorkOpen) => set((s) => ({ ui: { ...s.ui, newWorkOpen } })),
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
