import { ReactFlowProvider } from '@xyflow/react';
import { Canvas } from '../canvas/Canvas';
import { Panel } from '../panel/Panel';
import { TopBar } from './TopBar';
import styles from './AppShell.module.css';

export function AppShell() {
  return (
    <ReactFlowProvider>
      <div className={styles.shell}>
        <TopBar />
        <div className={styles.body}>
          <main className={styles.canvas}>
            <Canvas />
          </main>
          <Panel />
        </div>
      </div>
    </ReactFlowProvider>
  );
}
