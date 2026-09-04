import { ReactFlowProvider } from '@xyflow/react';
import { Canvas } from '../canvas/Canvas';
import { Panel } from '../panel/Panel';
import { DebugDrawer } from '../sim/DebugDrawer';
import { TaskEditor } from '../tasklist/TaskEditor';
import { TaskList } from '../tasklist/TaskList';
import { useAppStore } from '../store/store';
import { ConnectionBanner } from './ConnectionBanner';
import { TopBar } from './TopBar';
import styles from './AppShell.module.css';

export function AppShell() {
  const view = useAppStore((s) => s.ui.view);
  // Above the views, so the list's scrolling box cannot clip it.
  const editing = useAppStore((s) => s.world.tasks[s.ui.editingTaskId ?? '']);
  const hasData = useAppStore((s) => Object.keys(s.world.projects).length > 0);
  // The provider stays outside the switch: the attention chip fits the view
  // from the top bar, whichever view is showing.
  return (
    <ReactFlowProvider>
      <div className={styles.shell}>
        <TopBar />
        <ConnectionBanner hasData={hasData} />
        <div className={styles.body}>
          <main className={styles.canvas}>{view === 'canvas' ? <Canvas /> : <TaskList />}</main>
          {view === 'canvas' && <Panel />}
        </div>
        {editing && <TaskEditor key={editing.id} task={editing} />}
        <DebugDrawer />
      </div>
    </ReactFlowProvider>
  );
}
