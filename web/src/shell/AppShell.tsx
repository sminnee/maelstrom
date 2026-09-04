import { ReactFlowProvider } from '@xyflow/react';
import { useWorld } from '../api/useWorld';
import { Canvas } from '../canvas/Canvas';
import { Panel } from '../panel/Panel';
import { TaskEditor } from '../tasklist/TaskEditor';
import { TaskList } from '../tasklist/TaskList';
import { useAppStore } from '../store/store';
import { ConnectionBanner } from './ConnectionBanner';
import { TopBar } from './TopBar';
import styles from './AppShell.module.css';

export function AppShell() {
  const view = useAppStore((s) => s.ui.view);
  // Above the views, so the list's scrolling box cannot clip it.
  const editingTaskId = useAppStore((s) => s.ui.editingTaskId);
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
      </div>
    </ReactFlowProvider>
  );
}
