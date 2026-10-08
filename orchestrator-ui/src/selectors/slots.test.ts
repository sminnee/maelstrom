import { describe, expect, it } from 'vitest';
import { moveAnchor, showPane, togglePane, type SlotState } from './slots';

/** The opening state: the desk on the left, the panel on the right. */
const opening = (): SlotState => ({
  anchors: { canvas: 'left', list: 'left', worktrees: 'left', comms: 'left', tabs: 'right' },
  slots: { left: 'canvas', right: 'tabs' },
  paneRecency: ['canvas', 'tabs', 'list', 'worktrees'],
});

describe('showPane', () => {
  it('puts the pane in the slot of its anchor, and at the front of the recency', () => {
    const s = showPane(opening(), 'list');
    expect(s.slots).toEqual({ left: 'list', right: 'tabs' });
    expect(s.paneRecency).toEqual(['list', 'canvas', 'tabs', 'worktrees']);
  });
});

describe('togglePane', () => {
  it('keeps a showing pane at the front of the recency', () => {
    const s = togglePane(opening(), 'canvas');
    expect(s.paneRecency[0]).toBe('tabs');
  });

  it('reopens the left slot on a pane never selected, when no other is anchored left', () => {
    // The desk moves right and takes the only open slot; closing it leaves
    // Tasks as the most recent pane still anchored left.
    const s = togglePane(moveAnchor(opening(), 'canvas'), 'canvas');
    expect(s.slots).toEqual({ left: 'list', right: null });
  });

  it('does nothing when the last slot closes and no pane is anchored left', () => {
    const allRight: SlotState = {
      anchors: {
        canvas: 'right',
        list: 'right',
        worktrees: 'right',
        comms: 'right',
        tabs: 'right',
      },
      slots: { left: null, right: 'tabs' },
      paneRecency: ['tabs', 'canvas'],
    };
    expect(togglePane(allRight, 'tabs')).toBe(allRight);
  });
});

describe('moveAnchor', () => {
  it('shows two main views together once one is anchored right', () => {
    let s = moveAnchor(opening(), 'list');
    expect(s.slots).toEqual({ left: 'canvas', right: 'list' });
    // The panel still has its anchor, so a click brings it back over the list.
    s = showPane(s, 'tabs');
    expect(s.slots).toEqual({ left: 'canvas', right: 'tabs' });
  });

  it('shows a pane that was not showing, on its new side', () => {
    const s = moveAnchor(opening(), 'worktrees');
    expect(s.slots).toEqual({ left: 'canvas', right: 'worktrees' });
  });
});
