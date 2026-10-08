import { useCallback, useEffect, useState, type CSSProperties } from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import { useWorld } from '../api/useWorld';
import { Canvas } from '../canvas/Canvas';
import { Panel } from '../panel/Panel';
import { NewWork } from '../newwork/NewWork';
import { TaskEditor } from '../tasklist/TaskEditor';
import { TaskList } from '../tasklist/TaskList';
import { WorktreeTable } from '../worktrees/WorktreeTable';
import { useLayoutMode } from '../layout/useLayoutMode';
import { useShowing } from '../layout/useShowing';
import { useLocSync } from '../nav/useLocSync';
import { useLoc } from '../nav/useNav';
import { useAppStore } from '../store/store';
import type { Pane, Side, View } from '../store/uiSlice';
import { ConnectionBanner } from './ConnectionBanner';
import { HostBanner } from './HostBanner';
import { MobileShell } from './MobileShell';
import { TopBar } from './TopBar';
import styles from './AppShell.module.css';

/** Room for the worktree sidebar, about 150px, beside a strip still wide enough to read. */
const MIN_PANEL_WIDTH = 480;
/** The left slot that stays visible however wide the right one is dragged, so the grip stays reachable. */
const MIN_MAIN_STRIP = 48;
const clamp = (width: number) =>
  Math.max(MIN_PANEL_WIDTH, Math.min(window.innerWidth - MIN_MAIN_STRIP, width));

const MAIN_VIEWS: View[] = ['canvas', 'list', 'worktrees'];

/**
 * The app under the top bar, in one of the three layouts of `LayoutMode`.
 *
 * The branch is here, in TypeScript, because a media query cannot unmount
 * React Flow — and the narrow layout must not mount it at all.
 */
export function AppShell() {
  useLocSync();
  if (useLayoutMode() === 'narrow') return <MobileShell />;
  return <SlotShell />;
}

/** The wide and the medium layout: every showing pane, each in its slot. */
function SlotShell() {
  const showing = useShowing();
  const anchors = useAppStore((s) => s.ui.anchors);
  const panelWidth = useAppStore((s) => s.ui.panelWidth);
  // Above the views, so the list's scrolling box cannot clip it.
  const { edit: editingTaskId, newWork: newWorkOpen } = useLoc();
  const { status } = useWorld();
  // The clamp also holds after a window resize, not only during a drag.
  const [, resized] = useState(0);
  useEffect(() => {
    const onResize = () => resized((n) => n + 1);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const split = showing.length === 2;
  // The slot is set by `order`, not by JSX position: a pane that changes side
  // must move, not remount.
  const slot = (pane: Pane): { 'data-slot': Side; style: CSSProperties } => {
    const side = anchors[pane];
    return {
      'data-slot': side,
      style:
        split && side === 'right'
          ? { order: 2, flex: 'none', width: clamp(panelWidth) }
          : { order: side === 'left' ? 0 : 2 },
    };
  };
  // The provider stays outside the switch: the attention chip fits the view
  // from the top bar, whichever view is showing.
  return (
    <ReactFlowProvider>
      <div className={styles.shell}>
        <TopBar />
        <ConnectionBanner hasData={status === 'ready'} />
        <HostBanner />
        <main className={styles.body}>
          {MAIN_VIEWS.filter((view) => showing.includes(view)).map((view) => (
            <section key={view} className={styles.slot} {...slot(view)}>
              {view === 'canvas' ? (
                <Canvas />
              ) : view === 'worktrees' ? (
                <WorktreeTable />
              ) : (
                <TaskList />
              )}
            </section>
          ))}
          {split && <Grip width={clamp(panelWidth)} />}
          {/* Hidden rather than unmounted: a session tab holds its scroll
              position, window floor and pending waits across a toggle. */}
          <Panel hidden={!showing.includes('tabs')} {...slot('tabs')} />
        </main>
        {editingTaskId && <TaskEditor key={editingTaskId} taskId={editingTaskId} />}
        {newWorkOpen && <NewWork />}
      </div>
    </ReactFlowProvider>
  );
}

/** The line between two open slots. A drag on it resizes the right one. */
function Grip({ width }: { width: number }) {
  const setPanelWidth = useAppStore((s) => s.setPanelWidth);
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
    <div
      className={styles.grip}
      data-testid="grip"
      onPointerDown={onPointerDown}
      aria-hidden="true"
    />
  );
}
