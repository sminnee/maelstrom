import { Canvas } from '../canvas/Canvas';
import { TopBar } from './TopBar';
import styles from './AppShell.module.css';

export function AppShell() {
  return (
    <div className={styles.shell}>
      <TopBar />
      <div className={styles.body}>
        <main className={styles.canvas}>
          <Canvas />
        </main>
      </div>
    </div>
  );
}
