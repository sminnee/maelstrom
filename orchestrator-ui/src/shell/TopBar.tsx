import { useLayoutMode } from '../layout/useLayoutMode';
import { useShowing } from '../layout/useShowing';
import { useAppStore } from '../store/store';
import type { Pane, Side, View } from '../store/uiSlice';
import { Link } from 'react-router';
import { useGo, useHrefFor } from '../nav/useNav';
import { AgentsChip } from './AgentsChip';
import { AttentionChip } from './AttentionChip';
import { FilterBar } from './FilterBar';
import { UsageChips } from './UsageChips';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
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
            {actionIcon('back')}
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
          <AppButton
            variant="primary"
            icon={actionIcon('new')}
            onClick={() => setNewWorkOpen(true)}
          >
            New
          </AppButton>
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
      <AppButton variant="primary" icon={actionIcon('new')} onClick={() => setNewWorkOpen(true)}>
        New
      </AppButton>
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
      <AppButton
        variant="primary"
        icon={actionIcon('new')}
        onClick={() => {
          onClose();
          setNewWorkOpen(true);
        }}
      >
        New
      </AppButton>
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
      {panes.map(({ pane, label }) => {
        const props = {
          label,
          side,
          on: showing.includes(pane),
          title:
            side === null ? undefined : `Shift-click to move ${label} to the ${otherSide(side)}`,
        };
        return pane === 'tabs' ? (
          <TabsItem key={pane} {...props} />
        ) : (
          <ViewItem key={pane} view={pane} {...props} />
        );
      })}
    </div>
  );
}

interface ItemProps {
  label: string;
  side: Side | null;
  /** Whether the item's pane is on screen. */
  on: boolean;
  title: string | undefined;
}

/**
 * A main view's item: a link to the view, so it opens in a new window too. On the wide
 * layout a click on a showing view closes its slot and leaves the location; a shift-click
 * moves the view to the other side and makes it the location's view.
 */
function ViewItem({ view, label, side, on, title }: ItemProps & { view: View }) {
  const narrow = useLayoutMode() === 'narrow';
  const showPane = useAppStore((s) => s.showPane);
  const togglePane = useAppStore((s) => s.togglePane);
  const moveAnchor = useAppStore((s) => s.moveAnchor);
  const go = useGo();
  // Narrow: the card and the panel tab draw over the view, so a switch that kept them
  // would draw the old screen over the new view.
  const to = useHrefFor(narrow ? { view, card: null, panel: null } : { view });
  return (
    <Link
      to={to}
      className={styles.view}
      aria-current={on ? 'true' : undefined}
      title={title}
      onClick={(e) => {
        // A shift-click with no side to move to opens a new window, as on any link.
        if (e.metaKey || e.ctrlKey || e.altKey || e.button !== 0) return;
        if (side === null && e.shiftKey) return;
        if (side !== null && e.shiftKey) {
          e.preventDefault();
          moveAnchor(view);
          go({ view });
        } else if (side !== null && on) {
          e.preventDefault();
          togglePane(view);
        } else {
          // The location may name the view already, while another pane is in front.
          showPane(view);
        }
      }}
    >
      {label}
    </Link>
  );
}

/** The panel's item. The panel is not a location: it shows the tab the location names. */
function TabsItem({ label, side, on, title }: ItemProps) {
  const showPane = useAppStore((s) => s.showPane);
  const togglePane = useAppStore((s) => s.togglePane);
  const moveAnchor = useAppStore((s) => s.moveAnchor);
  return (
    <button
      type="button"
      className={styles.view}
      aria-pressed={on}
      title={title}
      onClick={(e) => {
        // One slot cannot close, and has no other side to move to.
        if (side === null) showPane('tabs');
        else if (e.shiftKey) moveAnchor('tabs');
        else togglePane('tabs');
      }}
    >
      {label}
    </button>
  );
}

const otherSide = (side: Side): Side => (side === 'left' ? 'right' : 'left');
