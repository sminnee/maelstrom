import { describe, expect, it } from 'vitest';
import { popScreen, pushScreen, type MobileScreen } from './navStack';

const detail: MobileScreen = { kind: 'detail', nodeId: 'p/1' };
const session: MobileScreen = { kind: 'session', agentId: 'a1' };
const document: MobileScreen = { kind: 'document', documentId: 'd1' };

describe('pushScreen', () => {
  it('puts the screen on top of the stack', () => {
    expect(pushScreen([], detail)).toEqual([detail]);
    expect(pushScreen([detail], session)).toEqual([detail, session]);
  });

  it('pushes nothing when the screen is already on top, so a repeat tap does not stack', () => {
    expect(pushScreen([detail], detail)).toEqual([detail]);
    expect(pushScreen([detail, session], { ...session })).toEqual([detail, session]);
  });

  it('returns to a screen already lower in the stack rather than pushing it twice', () => {
    expect(pushScreen([detail, session], detail)).toEqual([detail]);
    expect(pushScreen([detail, session, document], session)).toEqual([detail, session]);
  });
});

describe('popScreen', () => {
  it('takes the top screen off', () => {
    expect(popScreen([detail, session])).toEqual([detail]);
  });

  it('leaves an empty stack empty, so back at the deck is a no-op', () => {
    expect(popScreen([])).toEqual([]);
  });
});
