import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ApiProvider } from '../api/ApiProvider';
import { useAppStore } from '../store/store';
import { createFakeServer } from '../test/fakeServer';
import { PanelSidebar } from './PanelSidebar';
import { PanelTabs } from './PanelTabs';
import { sidebar, sidebarWorld } from './panelTabs.fixture';
import { WorktreeBar } from './WorktreeBar';
import { usePanelGroups } from './usePanelGroups';

export default { title: 'Panel / Sidebar' };

/**
 * The worktree sidebar beside the bar and strip it controls, drawn by the
 * real components. Check the project headings, `_main` first, the tab with
 * no worktree last, and the close cross on hover and on the row in view.
 *
 * The sidebar is one tab stop: Tab into it, then Up and Down between rows.
 */

/** The suite's own fake server, not a stub — see `TaskNode.stories.tsx`. */
const { api } = createFakeServer({ world: sidebarWorld });

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});

function Frame() {
  const { groups, activeGroup, close } = usePanelGroups();
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'auto minmax(0, 1fr)',
        height: 360,
        fontFamily: 'var(--font)',
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        overflow: 'hidden',
      }}
    >
      <PanelSidebar groups={groups} activeGroup={activeGroup} onClose={close} />
      <div style={{ minWidth: 0 }}>
        <WorktreeBar group={activeGroup} />
        <PanelTabs group={activeGroup} onClose={close} />
        <div style={{ padding: 'var(--space-5)', color: 'var(--fg-faint)' }}>The tab body.</div>
      </div>
    </div>
  );
}

export const TwoProjects: Story = () => {
  useState(() => {
    useAppStore.setState((s) => ({
      ui: { ...s.ui, tabs: sidebar.tabs, activeTabKey: sidebar.activeTabKey, tabRecency: [] },
    }));
  });
  return (
    <ApiProvider api={api} queryClient={queryClient}>
      <Frame />
    </ApiProvider>
  );
};
