import { useCallback } from 'react';
import { useAppStore } from '../store/store';
import type { PanelTab } from '../store/uiSlice';
import { SummaryTab } from '../summary/SummaryTab';
import { PANEL_BODY_ID, PanelTabs } from './PanelTabs';
import styles from './Panel.module.css';

/** The one right-hand region: a tab strip and the active tab's body. Resizable by drag. */
export function Panel() {
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const width = useAppStore((s) => s.ui.panelWidth);
  const setPanelWidth = useAppStore((s) => s.setPanelWidth);
  const active = tabs.find((t) => t.key === activeTabKey) ?? null;

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const grip = e.currentTarget;
      const startX = e.clientX;
      const startWidth = width;
      // Capturing the pointer keeps the drag alive when it leaves the window.
      grip.setPointerCapture(e.pointerId);
      const onMove = (ev: PointerEvent) => {
        setPanelWidth(Math.max(320, Math.min(900, startWidth - (ev.clientX - startX))));
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
    <aside className={styles.panel} style={{ width }} data-testid="panel">
      <div className={styles.grip} onPointerDown={onPointerDown} aria-hidden="true" />
      <PanelTabs />
      <div className={styles.body} role="tabpanel" id={PANEL_BODY_ID}>
        {active ? (
          <TabBody tab={active} />
        ) : (
          <div className={styles.empty}>Click a node to open it.</div>
        )}
      </div>
    </aside>
  );
}

function TabBody({ tab }: { tab: PanelTab }) {
  switch (tab.kind) {
    case 'summary':
      return <SummaryTab taskId={tab.taskId} />;
    case 'session':
      return <div className={styles.empty}>Session {tab.agentId}</div>;
    case 'document':
      return <div className={styles.empty}>Document {tab.documentId}</div>;
  }
}
