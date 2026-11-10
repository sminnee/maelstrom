import { describe, expect, it } from 'vitest';
import { moveAnchor, showPane, togglePane, type SlotState } from './slots';

/** The opening state: the desk on the left, the panel on the right. */
const opening = (): SlotState => ({
  anchors: {
    desk: 'left',
    list: 'left',
    comms: 'left',
    projects: 'left',
    tabs: 'right',
  },
  slots: { left: 'desk', right: 'tabs' },
  paneRecency: ['desk', 'tabs', 'list', 'comms'],
});

describe('showPane', () => {
  it('puts the pane in the slot of its anchor, and at the front of the recency', () => {
    const s = showPane(opening(), 'list');
    expect(s.slots).toEqual({ left: 'list', right: 'tabs' });
    expect(s.paneRecency).toEqual(['list', 'desk', 'tabs', 'comms']);
  });
});

describe('togglePane', () => {
  it('keeps a showing pane at the front of the recency', () => {
    const s = togglePane(opening(), 'desk');
    expect(s.paneRecency[0]).toBe('tabs');
  });

  it('reopens the left slot on a pane never selected, when no other is anchored left', () => {
    // The desk moves right and takes the only open slot; closing it leaves
    // Tasks as the most recent pane still anchored left.
    const s = togglePane(moveAnchor(opening(), 'desk'), 'desk');
    expect(s.slots).toEqual({ left: 'list', right: null });
  });

  it('does nothing when the last slot closes and no pane is anchored left', () => {
    const allRight: SlotState = {
      anchors: {
        desk: 'right',
        list: 'right',
        comms: 'right',
        projects: 'right',
        tabs: 'right',
      },
      slots: { left: null, right: 'tabs' },
      paneRecency: ['tabs', 'desk'],
    };
    expect(togglePane(allRight, 'tabs')).toBe(allRight);
  });
});

describe('moveAnchor', () => {
  it('shows two main views together once one is anchored right', () => {
    let s = moveAnchor(opening(), 'list');
    expect(s.slots).toEqual({ left: 'desk', right: 'list' });
    // The panel still has its anchor, so a click brings it back over the list.
    s = showPane(s, 'tabs');
    expect(s.slots).toEqual({ left: 'desk', right: 'tabs' });
  });

  it('shows a pane that was not showing, on its new side', () => {
    const s = moveAnchor(opening(), 'projects');
    expect(s.slots).toEqual({ left: 'desk', right: 'projects' });
  });
});
