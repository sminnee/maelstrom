import { create } from 'zustand';
import type { EventFrame } from '../protocol/events';
import type { ClientState } from '../protocol/reducer';
import { applyServerEvent, initialClientState } from '../protocol/reducer';
import type { TaskId } from '../protocol/ids';
import type { Filters, GroupBy } from '../selectors/filters';
import type { ListFilters } from '../selectors/taskList';
import type { PanelTab, UiState, View } from './uiSlice';
import { initialUiState } from './uiSlice';
import { closeTab as closeTabIn, openOrFocusTab } from '../selectors/tabs';

export interface AppStore extends ClientState {
  ui: UiState;
  applyFrame(frame: EventFrame): void;
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
  setDrawerOpen(open: boolean): void;
  setPanelWidth(width: number): void;
}

/** One store: the world as the reducer left it, plus client-only UI state. */
export const useAppStore = create<AppStore>()((set) => ({
  ...initialClientState(),
  ui: initialUiState(),
  applyFrame: (frame) => set((s) => applyServerEvent(s, frame)),
  reset: () => set({ ...initialClientState(), ui: initialUiState() }),
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
  expandNode: (taskId, toggle = true) =>
    set((s) => ({
      ui: { ...s.ui, expandedTaskId: toggle && s.ui.expandedTaskId === taskId ? null : taskId },
    })),
  collapseNode: () =>
    set((s) => (s.ui.expandedTaskId ? { ui: { ...s.ui, expandedTaskId: null } } : s)),
  setDrawerOpen: (drawerOpen) => set((s) => ({ ui: { ...s.ui, drawerOpen } })),
  setPanelWidth: (panelWidth) => set((s) => ({ ui: { ...s.ui, panelWidth } })),
}));
