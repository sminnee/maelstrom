import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { ConnectionState } from '../live/changeStream';
import type { TranscriptState } from '../live/transcriptReducer';
import type { AgentId, TaskId } from '../protocol/ids';
import type { Filters } from '../selectors/filters';
import type { ListFilters } from '../selectors/taskList';
import type { WorktreeFilters } from '../selectors/worktrees';
import type { NewWorkSeed, Pane, PanelTab, UiState } from './uiSlice';
import { initialUiState } from './uiSlice';
import {
  closeTabs as closeTabsIn,
  openTab as openTabIn,
  toggleSplit as toggleSplitIn,
  type TabState,
} from '../selectors/tabs';
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
  /**
   * Open a tab, or touch it as the active one, and show the panel. A split tab opened again
   * leaves the split and fills the body. The location names the active tab; this keeps the
   * open set in step with it.
   */
  openTab(tab: PanelTab): void;
  /**
   * Show a tab in the right half of its group's body, or take it out. `groupOf` as for
   * `closeTabs`. Returns the tab that is active after, which the caller moves the location to.
   */
  toggleSplit(
    key: string,
    groupOf: (tab: PanelTab) => string,
    active: string | null,
  ): string | null;
  /**
   * Close tabs. `groupOf` names each tab's worktree group, which the store
   * cannot work out: the world lives in the query cache, not here. Returns the tab that is
   * active after, as `toggleSplit` does.
   */
  closeTabs(
    keys: string[],
    groupOf: (tab: PanelTab) => string,
    active: string | null,
  ): string | null;
  /** Open the editor on a task, or close it with `null`. */
  setEditingTask(taskId: TaskId | null): void;
  /** Open or close the new-work form. A `seed` is what it opens on. */
  setNewWorkOpen(open: boolean, seed?: NewWorkSeed): void;
  /** Drop the seed once the form has taken it, so a remount does not lay it again. */
  clearNewWorkSeed(): void;
  setPanelWidth(width: number): void;
  /** Which zone the deck list shows. Narrow layout only. */
  setDeckZone(zone: Zone): void;
}

/** The store's tabs, with the active one the location names. */
const tabState = (ui: UiState, activeTabKey: string | null): TabState => ({
  tabs: ui.tabs,
  activeTabKey,
  tabRecency: ui.tabRecency,
  splitTabs: ui.splitTabs,
});

/** `ui` with a tab state laid over it. The active tab is the location's, so it stays out. */
const withTabs = (ui: UiState, { tabs, tabRecency, splitTabs }: TabState): UiState => ({
  ...ui,
  tabs,
  tabRecency,
  splitTabs,
});

/** Where the open tabs are kept across a refresh. Per window: a copied link opens its own. */
const TABS_STORAGE_KEY = 'mael-tabs';

/** The part of the store a refresh keeps: the open tabs, so the panel comes back as it was. */
type Kept = { ui: Pick<UiState, 'tabs' | 'tabRecency' | 'splitTabs'> };

/**
 * One store for what is not fetched: UI state, the connection state and the
 * open transcripts. The world itself lives in the query cache.
 */
export const useAppStore = create<AppStore>()(
  persist(
    (set, get) => ({
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
      setFilters: (patch) =>
        set((s) => ({ ui: { ...s.ui, filters: { ...s.ui.filters, ...patch } } })),
      setListFilters: (patch) =>
        set((s) => ({ ui: { ...s.ui, listFilters: { ...s.ui.listFilters, ...patch } } })),
      setWorktreeFilters: (patch) =>
        set((s) => ({ ui: { ...s.ui, worktreeFilters: { ...s.ui.worktreeFilters, ...patch } } })),
      // Opening a tab always shows the panel: a link must show what it opened.
      openTab: (tab) =>
        set((s) => {
          const ui = withTabs(s.ui, openTabIn(tabState(s.ui, null), tab));
          return { ui: { ...ui, ...showPaneIn(ui, 'tabs') } };
        }),
      toggleSplit: (key, groupOf, active) => {
        const next = toggleSplitIn(tabState(get().ui, active), key, groupOf);
        set((s) => ({ ui: withTabs(s.ui, next) }));
        return next.activeTabKey;
      },
      closeTabs: (keys, groupOf, active) => {
        const next = closeTabsIn(tabState(get().ui, active), keys, groupOf);
        set((s) => ({ ui: withTabs(s.ui, next) }));
        return next.activeTabKey;
      },
      setEditingTask: (editingTaskId) => set((s) => ({ ui: { ...s.ui, editingTaskId } })),
      setNewWorkOpen: (newWorkOpen, seed) =>
        set((s) => ({ ui: { ...s.ui, newWorkOpen, newWorkSeed: (newWorkOpen && seed) || null } })),
      clearNewWorkSeed: () =>
        set((s) => (s.ui.newWorkSeed ? { ui: { ...s.ui, newWorkSeed: null } } : s)),
      setPanelWidth: (panelWidth) => set((s) => ({ ui: { ...s.ui, panelWidth } })),
      setDeckZone: (deckZone) => set((s) => ({ ui: { ...s.ui, deckZone } })),
    }),
    {
      name: TABS_STORAGE_KEY,
      // Bump when `PanelTab` changes shape: a kept set of another version is dropped.
      version: 1,
      storage: createJSONStorage(() => sessionStorage),
      partialize: ({ ui }): Kept => ({
        ui: { tabs: ui.tabs, tabRecency: ui.tabRecency, splitTabs: ui.splitTabs },
      }),
      // Laid over the UI state, not in place of it: the rest of it is not kept.
      merge: (kept, current) => ({
        ...current,
        ui: { ...current.ui, ...(kept as Kept | undefined)?.ui },
      }),
    },
  ),
);

/**
 * Reset the store as a page load leaves it: empty, but for the tabs a refresh keeps. A fresh
 * `reset` would write its empty tabs over them. Returns whether any were kept.
 *
 * Session storage answers at once, so the kept tabs are in the store when this returns.
 */
export function resetToPageLoad(): boolean {
  const kept = sessionStorage.getItem(TABS_STORAGE_KEY);
  useAppStore.getState().reset();
  if (!kept) return false;
  sessionStorage.setItem(TABS_STORAGE_KEY, kept);
  void useAppStore.persist.rehydrate();
  return true;
}
