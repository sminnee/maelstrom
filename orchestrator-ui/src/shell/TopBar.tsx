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

/**
 * The screen strip of a pushed screen of the narrow layout: the way back, what
 * the screen is, where its actions go, and the side sheet's button.
 */
export interface StripState {
  title: string;
  onBack: () => void;
  sheetOpen: boolean;
  onMore: () => void;
}

/**
 * The top bar. On the narrow layout `back` replaces it with the screen strip:
 * see DESIGN.md, "The One Strip Rule".
 */
export function TopBar({
  back,
  actionsTarget,
}: {
  back?: StripState;
  /** Receives the element the screen's actions portal into. */
  actionsTarget?: (el: HTMLElement | null) => void;
}) {
  const setNewWorkOpen = useAppStore((s) => s.setNewWorkOpen);
  const mode = useLayoutMode();
  const narrow = mode === 'narrow';
  if (narrow && back) {
    return (
      <header className={styles.bar} data-narrow data-testid="top-bar">
        <h1 className="srOnly">maelstrom</h1>
        <div className={styles.row}>
          <button type="button" className={styles.back} aria-label="Back" onClick={back.onBack}>
            ←
          </button>
          <span className={styles.screenTitle} data-testid="screen-title">
            {back.title}
          </span>
          <span className={styles.actions} ref={actionsTarget} />
          {/* A chip that reads 0 is noise on the screen strip. */}
          <AttentionChip hideWhenClear />
          <button
            type="button"
            className={styles.more}
            aria-label="More"
            aria-haspopup="dialog"
            aria-expanded={back.sheetOpen}
            onClick={back.onMore}
          >
            ⋯
          </button>
        </div>
      </header>
    );
  }
  if (narrow) {
    return (
      <header className={styles.bar} data-narrow data-testid="top-bar">
        <div className={styles.row}>
          {/* At the narrow type scale the row cannot hold the brand beside the
              readings and both actions. The brand shows on the wide layout only;
              a screen reader keeps it here. */}
          <h1 className="srOnly">maelstrom</h1>
          <Readings />
          <div className={styles.spacer} />
          <AttentionChip />
          <button type="button" className={styles.new} onClick={() => setNewWorkOpen(true)}>
            New
          </button>
        </div>
        <PaneMenu side={null} />
      </header>
    );
  }
  return (
    <header className={styles.bar} data-testid="top-bar">
      {/* The mark is decoration: the word beside it already names the app. */}
      <h1 className={styles.brand}>
        <img src="/logo.svg" alt="" className={styles.mark} />
        maelstrom
      </h1>
      <PaneMenu side={mode === 'wide' ? 'left' : null} />
      <FilterBar />
      <div className={styles.spacer} />
      <Readings />
      <AttentionChip />
      {/* Over the slot its items show in. */}
      {mode === 'wide' && <PaneMenu side="right" />}
      {/* The one action ends the bar, as it does on the narrow layout. */}
      <button type="button" className={styles.new} onClick={() => setNewWorkOpen(true)}>
        New
      </button>
    </header>
  );
}

/**
 * The readings, as one group. The chips decide what a phone has room for, so
 * the bar never has to know what a reading means.
 */
function Readings() {
  return (
    <div className={styles.readings}>
      <UsageChips />
      <AgentsChip />
    </div>
  );
}

/**
 * The head of the side sheet: the readings and New, which the screen strip has
 * no room for. New closes the sheet, as anything that navigates does.
 */
export function SheetHead({ onClose }: { onClose: () => void }) {
  const setNewWorkOpen = useAppStore((s) => s.setNewWorkOpen);
  return (
    <div className={styles.sheetHead}>
      <Readings />
      <div className={styles.spacer} />
      <button
        type="button"
        className={styles.new}
        onClick={() => {
          onClose();
          setNewWorkOpen(true);
        }}
      >
        New
      </button>
    </div>
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
