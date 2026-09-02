import { SimControls } from '../sim/SimControls';
import { FilterBar } from './FilterBar';
import styles from './TopBar.module.css';

export function TopBar() {
  return (
    <header className={styles.bar}>
      <h1 className={styles.brand}>maelstrom</h1>
      <FilterBar />
      <div className={styles.spacer} />
      <SimControls />
    </header>
  );
}
