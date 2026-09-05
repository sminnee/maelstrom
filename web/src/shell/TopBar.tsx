import { useLayoutMode } from '../layout/useLayoutMode';
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
  const clearStack = useAppStore((s) => s.clearStack);
  const narrow = useLayoutMode() === 'narrow';
  return (
    <header className={styles.bar} data-narrow={narrow || undefined}>
      <h1 className={styles.brand}>maelstrom</h1>
      <div className={styles.views}>
        {VIEWS.map((v) => (
          <button
            key={v.view}
            type="button"
            className={styles.view}
            aria-pressed={view === v.view}
            onClick={() => {
              setView(v.view);
              // The stack sits over the view it was pushed from, so a switch
              // that left it standing would draw the old screen under a new view.
              clearStack();
            }}
          >
            {v.label}
          </button>
        ))}
      </div>
      {/* The narrow layout has no canvas to filter, and no room for the bar. */}
      {view === 'canvas' && !narrow && <FilterBar />}
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
