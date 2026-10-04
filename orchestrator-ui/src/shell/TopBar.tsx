import { useLayoutMode } from '../layout/useLayoutMode';
import { useShowing } from '../layout/useShowing';
import { useAppStore } from '../store/store';
import type { Pane, Side } from '../store/uiSlice';
import { AgentsChip } from './AgentsChip';
import { AttentionChip } from './AttentionChip';
import { FilterBar } from './FilterBar';
import { UsageChips } from './UsageChips';
import styles from './TopBar.module.css';

const PANES: { pane: Pane; label: string }[] = [
  { pane: 'canvas', label: 'Desk' },
  { pane: 'list', label: 'Tasks' },
  { pane: 'worktrees', label: 'Worktrees' },
  { pane: 'tabs', label: 'Tabs' },
];

/** The way back from a pushed screen of the narrow layout, and what the screen is. */
export interface BackRow {
  title: string;
  onBack: () => void;
}

/** The top bar. On the narrow layout `back` takes the second row: see DESIGN.md, "The narrow layout". */
export function TopBar({ back }: { back?: BackRow }) {
  const setNewWorkOpen = useAppStore((s) => s.setNewWorkOpen);
  const mode = useLayoutMode();
  const narrow = mode === 'narrow';
  if (narrow) {
    return (
      <header className={styles.bar} data-narrow data-testid="top-bar">
        <div className={styles.row}>
          {/* At the narrow type scale the row cannot hold the brand beside the
              readings and both actions. The brand shows on the wide layout only;
              a screen reader keeps it here. */}
          <h1 className="srOnly">maelstrom</h1>
          <div className={styles.readings}>
            <UsageChips />
            <AgentsChip />
          </div>
          <div className={styles.spacer} />
          <AttentionChip />
          <button type="button" className={styles.new} onClick={() => setNewWorkOpen(true)}>
            New
          </button>
        </div>
        {back ? (
          <div className={styles.row}>
            <button type="button" className={styles.back} onClick={back.onBack}>
              <span aria-hidden="true">←</span> Back
            </button>
            <span className={styles.screenTitle} data-testid="screen-title">
              {back.title}
            </span>
          </div>
        ) : (
          <PaneMenu side={null} />
        )}
      </header>
    );
  }
  return (
    <header className={styles.bar} data-testid="top-bar">
      <h1 className={styles.brand}>maelstrom</h1>
      <PaneMenu side={mode === 'wide' ? 'left' : null} />
      <FilterBar />
      <button type="button" className={styles.new} onClick={() => setNewWorkOpen(true)}>
        New
      </button>
      <div className={styles.spacer} />
      {/* The readings sit between New and the attention chip, so the one
          action and the one alarm keep the edges they already had. */}
      <div className={styles.readings}>
        {/* The chips decide what a phone has room for, so the bar never has
            to know what a reading means. */}
        <UsageChips />
        <AgentsChip />
      </div>
      <AttentionChip />
      {/* At the right edge, over the slot its items show in. */}
      {mode === 'wide' && <PaneMenu side="right" />}
    </header>
  );
}

/**
 * One group of menu items. The wide layout draws one group for each side, with
 * the items anchored there. The other layouts have one slot, so they pass
 * `null` and get one group.
 */
function PaneMenu({ side }: { side: Side | null }) {
  const mode = useLayoutMode();
  const showing = useShowing();
  const anchors = useAppStore((s) => s.ui.anchors);
  const showPane = useAppStore((s) => s.showPane);
  const togglePane = useAppStore((s) => s.togglePane);
  const moveAnchor = useAppStore((s) => s.moveAnchor);
  const clearStack = useAppStore((s) => s.clearStack);
  const panes = PANES.filter(({ pane }) =>
    // The narrow layout has no panel.
    side === null ? mode !== 'narrow' || pane !== 'tabs' : anchors[pane] === side,
  );
  // An empty group would draw as a bare border.
  if (panes.length === 0) return null;
  return (
    <div
      className={styles.views}
      role="group"
      aria-label={side === null ? 'Views' : side === 'left' ? 'Left slot' : 'Right slot'}
    >
      {panes.map(({ pane, label }) => (
        <button
          key={pane}
          type="button"
          className={styles.view}
          aria-pressed={showing.includes(pane)}
          title={
            side === null ? undefined : `Shift-click to move ${label} to the ${otherSide(side)}`
          }
          onClick={(e) => {
            // One slot cannot close, and has no other side to move to.
            if (side === null) showPane(pane);
            else if (e.shiftKey) moveAnchor(pane);
            else togglePane(pane);
            // The stack sits over the view it was pushed from, so a switch
            // that left it standing would draw the old screen under a new view.
            clearStack();
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

const otherSide = (side: Side): Side => (side === 'left' ? 'right' : 'left');
