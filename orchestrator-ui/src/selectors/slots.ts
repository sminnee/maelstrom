import type { LayoutMode } from '../layout/useLayoutMode';
import type { Pane, Side, UiState, View } from '../store/uiSlice';

/** What the slot rules read and write: the anchors, the slots and the recency. */
export type SlotState = Pick<UiState, 'anchors' | 'slots' | 'paneRecency'>;

const SIDES: Side[] = ['left', 'right'];
const other = (side: Side): Side => (side === 'left' ? 'right' : 'left');
const touch = (recency: Pane[], pane: Pane): Pane[] => [pane, ...recency.filter((p) => p !== pane)];

/** Put the pane in the slot of its anchor, and at the front of the recency. */
export function showPane(s: SlotState, pane: Pane): SlotState {
  return {
    ...s,
    slots: { ...s.slots, [s.anchors[pane]]: pane },
    paneRecency: touch(s.paneRecency, pane),
  };
}

/**
 * Show a pane that is not showing, or close the slot of one that is.
 *
 * The body is never empty: closing the last open slot reopens the left slot on
 * its most recent pane. With no pane anchored left there is nothing to reopen,
 * so the click does nothing.
 */
export function togglePane(s: SlotState, pane: Pane): SlotState {
  const side = s.anchors[pane];
  if (s.slots[side] !== pane) return showPane(s, pane);
  const remaining = s.slots[other(side)];
  if (remaining === null) {
    const reopen = s.paneRecency.find((p) => s.anchors[p] === 'left');
    if (reopen === undefined) return s;
    return showPane({ ...s, slots: { left: null, right: null } }, reopen);
  }
  // The medium layout draws the front of the recency, so it must be a pane
  // that is still showing.
  return {
    ...s,
    slots: { ...s.slots, [side]: null },
    paneRecency: touch(s.paneRecency, remaining),
  };
}

/** Move the pane's anchor to the other side, and show the pane there. */
export function moveAnchor(s: SlotState, pane: Pane): SlotState {
  const from = s.anchors[pane];
  return showPane(
    {
      ...s,
      anchors: { ...s.anchors, [pane]: other(from) },
      slots: s.slots[from] === pane ? { ...s.slots, [from]: null } : s.slots,
    },
    pane,
  );
}

/** The main view the narrow layout draws: it has no panel to show. */
export function mainView(s: SlotState): View {
  return s.paneRecency.find((p): p is View => p !== 'tabs') ?? 'canvas';
}

/**
 * The panes on screen, left first. The wide layout draws both slots, the
 * medium layout the one pane in front, and the narrow layout its main view.
 */
export function showing(s: SlotState, mode: LayoutMode): Pane[] {
  if (mode === 'narrow') return [mainView(s)];
  if (mode === 'medium') return s.paneRecency.slice(0, 1);
  return SIDES.map((side) => s.slots[side]).filter((p): p is Pane => p !== null);
}
