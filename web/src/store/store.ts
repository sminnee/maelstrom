import { create } from 'zustand';
import type { ConnectionState } from '../live/changeStream';
import type { TranscriptState } from '../live/transcriptReducer';
import type { AgentId, TaskId } from '../protocol/ids';
import type { Filters, GroupBy } from '../selectors/filters';
import type { ListFilters } from '../selectors/taskList';
import type { PanelTab, UiState, View } from './uiSlice';
import { initialUiState } from './uiSlice';
import { closeTab as closeTabIn, openOrFocusTab } from '../selectors/tabs';
import type { MobileScreen } from '../selectors/navStack';
import { popScreen, pushScreen } from '../selectors/navStack';
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
  setView(view: View): void;
  setGroupBy(groupBy: GroupBy): void;
  setFilters(patch: Partial<Filters>): void;
  setListFilters(patch: Partial<ListFilters>): void;
  openTab(tab: PanelTab): void;
  activateTab(key: string): void;
  closeTab(key: string): void;
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
  setView: (view) => set((s) => ({ ui: { ...s.ui, view } })),
  setGroupBy: (groupBy) => set((s) => ({ ui: { ...s.ui, groupBy } })),
  setFilters: (patch) => set((s) => ({ ui: { ...s.ui, filters: { ...s.ui.filters, ...patch } } })),
  setListFilters: (patch) =>
    set((s) => ({ ui: { ...s.ui, listFilters: { ...s.ui.listFilters, ...patch } } })),
  openTab: (tab) =>
    set((s) => {
      const tabs = openOrFocusTab(s.ui.tabs, tab);
      return { ui: { ...s.ui, tabs, activeTabKey: tab.key } };
    }),
  activateTab: (key) => set((s) => ({ ui: { ...s.ui, activeTabKey: key } })),
  closeTab: (key) =>
    set((s) => {
      const { tabs, activeTabKey } = closeTabIn(s.ui.tabs, s.ui.activeTabKey, key);
      return { ui: { ...s.ui, tabs, activeTabKey } };
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
