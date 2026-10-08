import type { CSSProperties, ReactNode } from 'react';
import { useAppStore } from '../store/store';
import type { PanelTab } from '../store/uiSlice';
import { ChangesTab } from '../changes/ChangesTab';
import { DevEnvTab } from '../devenv/DevEnvTab';
import { DocumentTab } from '../documents/DocumentTab';
import { SessionTab } from '../session/SessionTab';
import { PANEL_BODY_ID, PANEL_GROUP_ID, PanelTabs } from './PanelTabs';
import { PanelSidebar } from './PanelSidebar';
import { rowId, usePanelGroups } from './usePanelGroups';
import { usePanelNav } from './usePanelNav';
import { WorktreeBar } from './WorktreeBar';
import styles from './Panel.module.css';

/**
 * The pane the top bar calls `Tabs`: a sidebar of worktrees, and beside it the
 * worktree in view's bar, its tab strip and the active tab's body — with the
 * group's split tab beside it in the right half, when it has one.
 */
export function Panel({
  hidden = false,
  ...slot
}: {
  hidden?: boolean;
  style?: CSSProperties;
  'data-slot'?: string;
}) {
  const tabs = useAppStore((s) => s.ui.tabs);
  const { groups, activeGroup, close } = usePanelGroups();
  const { activeTabKey } = usePanelNav();
  const splitTabs = useAppStore((s) => s.ui.splitTabs);
  const active = tabs.find((t) => t.key === activeTabKey) ?? null;
  const splitKey = activeGroup ? splitTabs[activeGroup.key] : undefined;
  // From the group, not from every tab: a tab the world moves to another group leaves this one.
  const split = activeGroup?.tabs.find((t) => t.key === splitKey) ?? null;
  const sideOf = (tab: PanelTab): Side | null =>
    tab === active ? 'left' : tab === split ? 'right' : null;
  return (
    <aside className={styles.panel} data-testid="panel" hidden={hidden} {...slot}>
      <PanelSidebar groups={groups} activeGroup={activeGroup} onClose={close} />
      <div
        className={styles.group}
        id={PANEL_GROUP_ID}
        role={activeGroup ? 'tabpanel' : undefined}
        aria-labelledby={activeGroup ? rowId(activeGroup.key) : undefined}
      >
        <WorktreeBar group={activeGroup} />
        <PanelTabs group={activeGroup} onClose={close} />
        <div className={styles.body} role="tabpanel" id={PANEL_BODY_ID} data-testid="panel-body">
          {!active && (
            <div className={styles.empty}>Open a session or a document from a node or a task.</div>
          )}
          {[active, split].map(
            (tab) =>
              tab &&
              tab.kind !== 'devenv' && (
                <Half key={sideOf(tab)} side={sideOf(tab)}>
                  <TabBody tab={tab} onTakenOffDesk={() => close([tab.key])} />
                </Half>
              ),
          )}
          {/* Keyed by tab, not by half: a dev env frame survives a tab switch and a move between halves. */}
          {tabs.map(
            (tab) =>
              tab.kind === 'devenv' && (
                <Half key={tab.key} side={sideOf(tab)}>
                  <DevEnvTab worktreeId={tab.worktreeId} service={tab.service} />
                </Half>
              ),
          )}
        </div>
      </div>
    </aside>
  );
}

type Side = 'left' | 'right';

/**
 * One half of the body: the left holds the active tab, the right the split
 * tab. With no split the left fills the body. CSS `order` places a half, so
 * the order of the children never has to change.
 */
function Half({ side, children }: { side: Side | null; children: ReactNode }) {
  return (
    <div className={styles.half} data-side={side ?? undefined} hidden={!side}>
      {children}
    </div>
  );
}

function TabBody({
  tab,
  onTakenOffDesk,
}: {
  tab: Exclude<PanelTab, { kind: 'devenv' }>;
  onTakenOffDesk: () => void;
}) {
  switch (tab.kind) {
    case 'session':
      // Keyed, as the document tab below is: the tab holds a scroll position,
      // a window floor and a pending compact wait, all of which belong to one
      // agent. A reused fiber opens the next agent at the last one's state.
      return <SessionTab key={tab.agentId} agentId={tab.agentId} onTakenOffDesk={onTakenOffDesk} />;
    case 'document':
      // Keyed: the tab holds per-document mutation state, so a reused
      // fiber would show one document's created tasks under the next.
      return <DocumentTab key={tab.documentId} documentId={tab.documentId} />;
    case 'changes':
      // Keyed: the tab holds the rev it shows, which belongs to one worktree.
      return <ChangesTab key={tab.worktreeId} worktreeId={tab.worktreeId} />;
  }
}
