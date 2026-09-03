import { ReactFlowProvider } from '@xyflow/react';
import { Canvas } from '../canvas/Canvas';
import { Panel } from '../panel/Panel';
import { DebugDrawer } from '../sim/DebugDrawer';
import { TaskList } from '../tasklist/TaskList';
import { useAppStore } from '../store/store';
import { TopBar } from './TopBar';
import styles from './AppShell.module.css';

export function AppShell() {
  const view = useAppStore((s) => s.ui.view);
  // The provider stays outside the switch: the attention chip fits the view
  // from the top bar, whichever view is showing.
  return (
    <ReactFlowProvider>
      <div className={styles.shell}>
        <TopBar />
        <div className={styles.body}>
          <main className={styles.canvas}>{view === 'canvas' ? <Canvas /> : <TaskList />}</main>
          {view === 'canvas' && <Panel />}
        </div>
        <DebugDrawer />
      </div>
    </ReactFlowProvider>
  );
}
