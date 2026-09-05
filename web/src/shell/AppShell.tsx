import { ReactFlowProvider } from '@xyflow/react';
import { useWorld } from '../api/useWorld';
import { Canvas } from '../canvas/Canvas';
import { Panel } from '../panel/Panel';
import { NewWork } from '../newwork/NewWork';
import { TaskEditor } from '../tasklist/TaskEditor';
import { TaskList } from '../tasklist/TaskList';
import { useLayoutMode } from '../layout/useLayoutMode';
import { useAppStore } from '../store/store';
import { ConnectionBanner } from './ConnectionBanner';
import { MobileShell } from './MobileShell';
import { TopBar } from './TopBar';
import styles from './AppShell.module.css';

/**
 * The app under the top bar. Two layouts, chosen by viewport width.
 *
 * The wide layout is the main-monitor tool: the canvas or the task list, with
 * the panel beside it. The narrow layout has room for neither the board nor a
 * second column, so it draws the deck list and pushes one screen at a time.
 * The branch is here, in TypeScript, because a media query cannot unmount
 * React Flow — and the narrow layout must not mount it at all.
 */
export function AppShell() {
  if (useLayoutMode() === 'narrow') return <MobileShell />;
  return <WideShell />;
}

function WideShell() {
  const view = useAppStore((s) => s.ui.view);
  // Above the views, so the list's scrolling box cannot clip it.
  const editingTaskId = useAppStore((s) => s.ui.editingTaskId);
  const newWorkOpen = useAppStore((s) => s.ui.newWorkOpen);
  const { status } = useWorld();
  // The provider stays outside the switch: the attention chip fits the view
  // from the top bar, whichever view is showing.
  return (
    <ReactFlowProvider>
      <div className={styles.shell}>
        <TopBar />
        <ConnectionBanner hasData={status === 'ready'} />
        <div className={styles.body}>
          <main className={styles.canvas}>{view === 'canvas' ? <Canvas /> : <TaskList />}</main>
          {view === 'canvas' && <Panel />}
        </div>
        {editingTaskId && <TaskEditor key={editingTaskId} taskId={editingTaskId} />}
        {newWorkOpen && <NewWork />}
      </div>
    </ReactFlowProvider>
  );
}
