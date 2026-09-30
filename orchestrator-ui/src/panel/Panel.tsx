import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../store/store';
import type { PanelTab } from '../store/uiSlice';
import { ChangesTab } from '../changes/ChangesTab';
import { DocumentTab } from '../documents/DocumentTab';
import { SessionTab } from '../session/SessionTab';
import { PANEL_BODY_ID, PANEL_GROUP_ID, PanelTabs } from './PanelTabs';
import { PanelSidebar } from './PanelSidebar';
import { rowId, usePanelGroups } from './usePanelGroups';
import { WorktreeBar } from './WorktreeBar';
import styles from './Panel.module.css';

/** Room for the worktree sidebar, about 150px, beside a strip still wide enough to read. */
const MIN_PANEL_WIDTH = 480;
/** The main view that stays visible however wide the panel is dragged, so the grip stays reachable. */
const MIN_MAIN_STRIP = 48;
const clamp = (width: number) =>
  Math.max(MIN_PANEL_WIDTH, Math.min(window.innerWidth - MIN_MAIN_STRIP, width));

/**
 * The one right-hand region, resizable by drag: a sidebar of worktrees, and
 * beside it the worktree in view's bar, its tab strip and the active tab's body.
 */
export function Panel({ hidden = false }: { hidden?: boolean }) {
  const tabs = useAppStore((s) => s.ui.tabs);
  const { groups, activeGroup, close } = usePanelGroups();
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const width = useAppStore((s) => s.ui.panelWidth);
  const setPanelWidth = useAppStore((s) => s.setPanelWidth);
  const active = tabs.find((t) => t.key === activeTabKey) ?? null;
  // The clamp also holds after a window resize, not only during a drag.
  const [, resized] = useState(0);
  useEffect(() => {
    const onResize = () => resized((n) => n + 1);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const grip = e.currentTarget;
      const startX = e.clientX;
      const startWidth = width;
      // Capturing the pointer keeps the drag alive when it leaves the window.
      grip.setPointerCapture(e.pointerId);
      const onMove = (ev: PointerEvent) => {
        setPanelWidth(clamp(startWidth - (ev.clientX - startX)));
      };
      const onEnd = () => {
        grip.removeEventListener('pointermove', onMove);
        grip.removeEventListener('pointerup', onEnd);
        grip.removeEventListener('pointercancel', onEnd);
      };
      grip.addEventListener('pointermove', onMove);
      grip.addEventListener('pointerup', onEnd);
      grip.addEventListener('pointercancel', onEnd);
    },
    [width, setPanelWidth],
  );

  return (
    <aside
      className={styles.panel}
      style={{ width: clamp(width) }}
      data-testid="panel"
      hidden={hidden}
    >
      <div className={styles.grip} onPointerDown={onPointerDown} aria-hidden="true" />
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
          {active ? (
            <TabBody tab={active} />
          ) : (
            <div className={styles.empty}>Open a session or a document from a node or a task.</div>
          )}
        </div>
      </div>
    </aside>
  );
}

function TabBody({ tab }: { tab: PanelTab }) {
  switch (tab.kind) {
    case 'session':
      // Keyed, as the document tab below is: the tab holds a scroll position,
      // a window floor and a pending compact wait, all of which belong to one
      // agent. A reused fiber opens the next agent at the last one's state.
      return <SessionTab key={tab.agentId} agentId={tab.agentId} />;
    case 'document':
      // Keyed: the tab holds per-document mutation state, so a reused
      // fiber would show one document's created tasks under the next.
      return <DocumentTab key={tab.documentId} documentId={tab.documentId} />;
    case 'changes':
      // Keyed: the tab holds the rev it shows, which belongs to one worktree.
      return <ChangesTab key={tab.worktreeId} worktreeId={tab.worktreeId} />;
  }
}
