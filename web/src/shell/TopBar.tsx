import { useAppStore } from '../store/store';
import type { View } from '../store/uiSlice';
import { AttentionChip } from './AttentionChip';
import { FilterBar } from './FilterBar';
import styles from './TopBar.module.css';

const VIEWS: { view: View; label: string }[] = [
  { view: 'canvas', label: 'Canvas' },
  { view: 'list', label: 'Task list' },
];

export function TopBar() {
  const view = useAppStore((s) => s.ui.view);
  const setView = useAppStore((s) => s.setView);
  const setNewWorkOpen = useAppStore((s) => s.setNewWorkOpen);
  return (
    <header className={styles.bar}>
      <h1 className={styles.brand}>maelstrom</h1>
      <div className={styles.views}>
        {VIEWS.map((v) => (
          <button
            key={v.view}
            type="button"
            className={styles.view}
            aria-pressed={view === v.view}
            onClick={() => setView(v.view)}
          >
            {v.label}
          </button>
        ))}
      </div>
      {view === 'canvas' && <FilterBar />}
      <div className={styles.spacer}>
        {/* In both views, so the affordance never moves. */}
        <button type="button" className={styles.new} onClick={() => setNewWorkOpen(true)}>
          New
        </button>
      </div>
      <AttentionChip />
    </header>
  );
}
