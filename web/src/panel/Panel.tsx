import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../store/store';
import type { PanelTab } from '../store/uiSlice';
import { DocumentTab } from '../documents/DocumentTab';
import { SessionTab } from '../session/SessionTab';
import { SummaryTab } from '../summary/SummaryTab';
import { PANEL_BODY_ID, PanelTabs } from './PanelTabs';
import styles from './Panel.module.css';

const MIN_PANEL_WIDTH = 320;
/** The canvas that stays visible however wide the panel is dragged, so the grip stays reachable. */
const MIN_CANVAS_STRIP = 48;
const clamp = (width: number) =>
  Math.max(MIN_PANEL_WIDTH, Math.min(window.innerWidth - MIN_CANVAS_STRIP, width));

/** The one right-hand region: a tab strip and the active tab's body. Resizable by drag. */
export function Panel() {
  const tabs = useAppStore((s) => s.ui.tabs);
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
    <aside className={styles.panel} style={{ width: clamp(width) }} data-testid="panel">
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
      return <SessionTab agentId={tab.agentId} />;
    case 'document':
      return <DocumentTab documentId={tab.documentId} />;
  }
}
