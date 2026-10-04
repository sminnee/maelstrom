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
import { WorktreeBar } from './WorktreeBar';
import styles from './Panel.module.css';

/**
 * The pane the top bar calls `Tabs`: a sidebar of worktrees, and beside it the
 * worktree in view's bar, its tab strip and the active tab's body.
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
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const active = tabs.find((t) => t.key === activeTabKey) ?? null;
  const sideOf = (tab: PanelTab): Side | null => (tab === active ? 'left' : null);
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
          {active && active.kind !== 'devenv' && (
            <Half side="left">
              <TabBody tab={active} onTakenOffDesk={() => close([active.key])} />
            </Half>
          )}
          {/* Every dev env tab stays mounted, hidden when out of view, so its app keeps its state. */}
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

type Side = 'left';

/** The part of the body that shows a tab. A tab out of view is hidden. */
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
