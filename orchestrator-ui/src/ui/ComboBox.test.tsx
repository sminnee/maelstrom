import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ComboBox, type ComboOption } from './ComboBox';

const BRANCHES: ComboOption[] = [
  { value: 'feat/orders' },
  { value: 'feat/logs' },
  { value: 'fix/export' },
];

const ISSUES: ComboOption[] = [
  { value: 'MAEL-1', label: 'Add a Linear kind' },
  { value: 'MAEL-2', label: 'Drop the old panel' },
];

/**
 * Render a ComboBox as the app holds it: controlled, its value fed back in.
 * `onChange` reports what it was asked to change to.
 */
function setup(options: ComboOption[], initial = '') {
  const onChange = vi.fn();
  function Harness() {
    const [value, setValue] = useState(initial);
    return (
      <label>
        <span>Branch</span>
        <ComboBox
          value={value}
          options={options}
          onChange={(v) => {
            onChange(v);
            setValue(v);
          }}
        />
      </label>
    );
  }
  render(<Harness />);
  return { onChange, input: screen.getByLabelText('Branch') };
}

/** A visual viewport the test can resize, as a soft keyboard does. */
function stubVisualViewport(height: number, offsetTop = 0) {
  const viewport = Object.assign(new EventTarget(), { height, offsetTop });
  vi.stubGlobal('visualViewport', viewport);
  return viewport;
}

/** Whether the browser lays out CSS anchors. jsdom does not; iOS before 26 does not either. */
const anchorsSupported = (yes: boolean) =>
  vi
    .spyOn(CSS, 'supports')
    .mockImplementation((property: string, value?: string) =>
      property === 'top' && value === 'anchor(bottom)' ? yes : false,
    );

/**
 * Lay the offer out as a browser would, `drift` px lower than its `top` says
 * and `height` tall. On iOS the drawn box and its offsets disagree, so the
 * offer is checked by where it is drawn, not by the offsets it wrote.
 */
function drawOffer(drift: number, height = () => 60) {
  vi.spyOn(HTMLUListElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLUListElement,
  ) {
    const top = parseFloat(this.style.top) + drift;
    return DOMRect.fromRect({
      x: parseFloat(this.style.left),
      y: top,
      width: 300,
      height: height(),
    });
  });
  return () => screen.getByRole('listbox').getBoundingClientRect();
}

/** Put the field `top` px down, `height` tall. The returned call moves it. */
function placeField(
  input: HTMLElement,
  {
    top,
    height = 26,
    left = 0,
    width = 100,
  }: { top: number; height?: number; left?: number; width?: number },
) {
  let at = top;
  input.getBoundingClientRect = () => DOMRect.fromRect({ x: left, y: at, width, height });
  return (to: number) => {
    at = to;
  };
}

/** Run `fire`, then the animation frame after it. */
function afterFrame(fire: () => void) {
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
  act(() => {
    fire();
    vi.advanceTimersToNextFrame();
  });
}

/** A touch screen: one whose focus brings up a soft keyboard. */
const touchScreen = () =>
  vi
    .spyOn(window, 'matchMedia')
    .mockImplementation(
      (query: string) =>
        ({ matches: query === '(pointer: coarse)', media: query }) as unknown as MediaQueryList,
    );

/** The open listbox's rows, in the order they are offered. */
function rows() {
  return within(screen.getByRole('listbox'))
    .getAllByRole('option')
    .map((o) => o.textContent);
}

describe('ComboBox', () => {
  // The placement tests spy on `innerHeight` and `scrollHeight`. `restoreMocks`
  // is not set, so without this they would leak into the tests that follow.
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('is labelled and reachable as a combobox', () => {
    const { input } = setup(BRANCHES);
    expect(input).toHaveRole('combobox');
    expect(input).toHaveAttribute('aria-expanded', 'false');
    // Closed, there is no list to read.
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('offers every option once opened', async () => {
    const user = userEvent.setup();
    const { input } = setup(BRANCHES);
    await user.click(input);
    expect(input).toHaveAttribute('aria-expanded', 'true');
    expect(rows()).toEqual(['feat/orders', 'feat/logs', 'fix/export']);
  });

  it('narrows the offer to what was typed, matching anywhere in the value', async () => {
    const user = userEvent.setup();
    const { input } = setup(BRANCHES);
    await user.type(input, 'log');
    expect(rows()).toEqual(['feat/logs']);
  });

  it('reports each keystroke, so free text that matches nothing is still kept', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(BRANCHES);
    await user.type(input, 'feat/nope');
    expect(onChange).toHaveBeenLastCalledWith('feat/nope');
    // Nothing matches, and the control says so rather than showing an empty box.
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('chooses the clicked option and closes', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(BRANCHES);
    await user.click(input);
    await user.click(screen.getByRole('option', { name: 'feat/logs' }));
    expect(onChange).toHaveBeenLastCalledWith('feat/logs');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('walks the offer with the arrow keys and takes one with Enter', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(BRANCHES);
    await user.click(input);
    await user.keyboard('{ArrowDown}{ArrowDown}');
    // The active row is named, so a screen reader follows the walk.
    const active = screen.getByRole('option', { name: 'feat/logs' });
    expect(input).toHaveAttribute('aria-activedescendant', active.id);
    await user.keyboard('{Enter}');
    expect(onChange).toHaveBeenLastCalledWith('feat/logs');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('closes on Escape without choosing anything', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(BRANCHES);
    await user.click(input);
    await user.keyboard('{ArrowDown}{Escape}');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('closes when the focus leaves it', async () => {
    const user = userEvent.setup();
    const { input } = setup(BRANCHES);
    await user.click(input);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    await user.tab();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('shows a label beside its value in the list, and only the value in the field', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(ISSUES);
    await user.click(input);
    expect(rows()).toEqual(['MAEL-1Add a Linear kind', 'MAEL-2Drop the old panel']);
    await user.click(screen.getByRole('option', { name: /Add a Linear kind/ }));
    // The field carries the value alone: the label is there to choose by.
    expect(onChange).toHaveBeenLastCalledWith('MAEL-1');
  });

  it('matches on the label too, so an issue is findable by its words', async () => {
    const user = userEvent.setup();
    const { input } = setup(ISSUES);
    await user.type(input, 'old panel');
    expect(rows()).toEqual(['MAEL-2Drop the old panel']);
  });

  it('matches within a field, never across the gap between them', async () => {
    const user = userEvent.setup();
    const { input } = setup(ISSUES);
    // `MAEL-1 Add` spans the id and its label; it is in neither field.
    await user.type(input, 'MAEL-1 Add');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('offers nothing when it has no options', async () => {
    const user = userEvent.setup();
    const { input } = setup([]);
    await user.click(input);
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('anchors the offer to the field, and writes no coordinates of its own', async () => {
    // jsdom lays out no anchors, so this asserts the contract rather than the pixels — the pair
    // is joined, and nothing measured the field to write `left`/`top` instead. Only a browser
    // shows a broken anchor, so this is a guard, not the proof.
    anchorsSupported(true);
    const user = userEvent.setup();
    const { input } = setup(BRANCHES);
    await user.click(input);
    const list = screen.getByRole('listbox');

    const anchor = input.style.getPropertyValue('--anchor-name');
    expect(anchor).not.toBe('');
    expect(list.style.getPropertyValue('--anchor-name')).toBe(anchor);
    expect(list).toHaveAttribute('popover');
    expect(list.style.left).toBe('');
    expect(list.style.top).toBe('');
  });

  it('opens upward only when the offer does not fit below the field', async () => {
    // A short window often leaves more room above the field than below it. That
    // alone must not flip the offer: what matters is whether it fits below,
    // because a flip a user did not need reads as a jump.
    const user = userEvent.setup();
    // 300px tall, field 200px down: 100px below, 200px above. A three-row offer
    // fits below; a full-height one does not.
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(300);
    const short = vi.spyOn(HTMLUListElement.prototype, 'scrollHeight', 'get').mockReturnValue(60);
    const { input } = setup(BRANCHES);
    // The control measures the field it is anchored to, so place that one.
    placeField(input, { top: 200 });

    await user.click(input);
    expect(screen.getByRole('listbox').dataset.position).toBe('bottom');

    // The same field, with an offer too tall for the room below it. Reopened by
    // typing: a click does not, because the focus never left the input.
    await user.keyboard('{Escape}');
    short.mockReturnValue(400);
    await user.type(input, 'f');
    const list = screen.getByRole('listbox');
    expect(list.dataset.position).toBe('top');
    // Capped to the room it has, so it cannot run off the top of the screen.
    expect(list.style.maxHeight).toBe('198px');
  });

  it('re-places the offer as typing narrows it', async () => {
    // Typing does not reopen the offer, so a placement made once at open goes
    // stale: an offer that had to open upward at full height still opens
    // upward after a keystroke cuts it to one row.
    const user = userEvent.setup();
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(300);
    const height = vi.spyOn(HTMLUListElement.prototype, 'scrollHeight', 'get').mockReturnValue(400);
    const { input } = setup(BRANCHES);
    // The control measures the field it is anchored to, so place that one.
    placeField(input, { top: 200 });

    await user.click(input);
    expect(screen.getByRole('listbox').dataset.position).toBe('top');

    // One row now, which fits in the 72px below the field.
    height.mockReturnValue(30);
    await user.type(input, 'log');
    expect(screen.getByRole('listbox').dataset.position).toBe('bottom');
  });

  it('places the offer itself where the browser lays out no anchors', async () => {
    anchorsSupported(false);
    const user = userEvent.setup();
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(900);
    const height = vi.spyOn(HTMLUListElement.prototype, 'scrollHeight', 'get').mockReturnValue(60);
    const { input } = setup(BRANCHES);
    placeField(input, { top: 700, left: 16, width: 300 });
    // The browser draws the offer 50px from where its offsets say.
    let drawnHeight = 60;
    const drawn = drawOffer(50, () => drawnHeight);

    await user.click(input);
    // Under the field, 2px down, at least as wide as it.
    expect(drawn().top).toBe(728);
    expect(drawn().left).toBe(16);
    expect(screen.getByRole('listbox').style.minWidth).toBe('300px');

    // Over the field: its foot 2px above the field's top.
    await user.keyboard('{Escape}');
    height.mockReturnValue(800);
    drawnHeight = 240;
    await user.type(input, 'f');
    expect(drawn().bottom).toBe(698);
  });

  it('measures the room below against the visible area, which a keyboard shrinks', async () => {
    // On iOS the keyboard shrinks the visual viewport and not the window, so
    // the window's height counts room the keyboard covers.
    const user = userEvent.setup();
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(900);
    vi.spyOn(HTMLUListElement.prototype, 'scrollHeight', 'get').mockReturnValue(200);
    const viewport = stubVisualViewport(900);
    const { input } = setup(BRANCHES);
    placeField(input, { top: 400 });

    await user.click(input);
    expect(screen.getByRole('listbox').dataset.position).toBe('bottom');

    // The keyboard opens after the focus, and leaves 74px under the field.
    // The offer places again a frame later, once the box has its new height.
    viewport.height = 500;
    afterFrame(() => viewport.dispatchEvent(new Event('resize')));
    expect(screen.getByRole('listbox').dataset.position).toBe('top');
  });

  it('measures the room against the visible area when iOS has panned the page', async () => {
    // The simulator's numbers: the page panned 278px, and the field's rect is
    // in the page's coordinates. 292px below it is visible, not 14px.
    const user = userEvent.setup();
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(461);
    vi.spyOn(HTMLUListElement.prototype, 'scrollHeight', 'get').mockReturnValue(200);
    stubVisualViewport(461, 278);
    const { input } = setup(BRANCHES);
    placeField(input, { top: 397, height: 48 });

    await user.click(input);
    expect(screen.getByRole('listbox').dataset.position).toBe('bottom');
  });

  it('is not placed again by its own scroll, which would clamp it', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(900);
    vi.spyOn(HTMLUListElement.prototype, 'scrollHeight', 'get').mockReturnValue(60);
    const { input } = setup(BRANCHES);
    placeField(input, { top: 200 });
    await user.click(input);
    const list = screen.getByRole('listbox');
    list.style.maxHeight = '100px';

    afterFrame(() => list.dispatchEvent(new Event('scroll')));
    expect(list.style.maxHeight).toBe('100px');

    afterFrame(() => document.body.dispatchEvent(new Event('scroll')));
    expect(list.style.maxHeight).toBe('');
  });

  it('follows the field when the form scrolls under it', async () => {
    // The dialog scrolls to the focused field after the keyboard opens, so an
    // offer placed by hand at focus is left where the field was.
    anchorsSupported(false);
    const user = userEvent.setup();
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(900);
    vi.spyOn(HTMLUListElement.prototype, 'scrollHeight', 'get').mockReturnValue(60);
    const { input } = setup(BRANCHES);
    const drawn = drawOffer(0);
    const moveField = placeField(input, { top: 600, left: 16, width: 300 });
    await user.click(input);
    expect(drawn().top).toBe(628);

    moveField(300);
    // A scroll does not bubble, so the offer listens in the capture phase.
    afterFrame(() => document.body.dispatchEvent(new Event('scroll')));
    expect(drawn().top).toBe(328);
  });

  describe('on a touch screen', () => {
    // The keyboard opens after the focus, and shrinks the box. An offer placed
    // before that is placed for a box that is about to change.
    const fakeTimers = () =>
      vi.useFakeTimers({
        toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'],
      });

    it('waits for the keyboard to open, then a frame, before it offers', () => {
      touchScreen();
      const viewport = stubVisualViewport(900);
      fakeTimers();
      const { input } = setup(BRANCHES);

      act(() => input.focus());
      expect(screen.queryByRole('listbox')).toBeNull();

      viewport.height = 500;
      act(() => {
        viewport.dispatchEvent(new Event('resize'));
      });
      expect(screen.queryByRole('listbox')).toBeNull();
      act(() => {
        vi.advanceTimersToNextFrame();
      });
      expect(screen.getByRole('listbox')).toBeInTheDocument();
    });

    it('opens above the field, away from the keyboard, even with room below', () => {
      touchScreen();
      const viewport = stubVisualViewport(461);
      fakeTimers();
      vi.spyOn(HTMLUListElement.prototype, 'scrollHeight', 'get').mockReturnValue(30);
      const { input } = setup(BRANCHES);
      // The probe's numbers from the simulator: 292px below, 117px above.
      const moveField = placeField(input, { top: 119, height: 48 });

      act(() => input.focus());
      act(() => {
        vi.advanceTimersByTime(400);
      });
      expect(screen.getByRole('listbox').dataset.position).toBe('top');

      // Under three rows above, and more room below: below it is.
      fireEvent.keyDown(input, { key: 'Escape' });
      moveField(60);
      fireEvent.change(input, { target: { value: 'f' } });
      expect(screen.getByRole('listbox').dataset.position).toBe('bottom');

      // Under three rows above, and less below: above it stays.
      fireEvent.keyDown(input, { key: 'Escape' });
      viewport.height = 150;
      fireEvent.change(input, { target: { value: 'fe' } });
      expect(screen.getByRole('listbox').dataset.position).toBe('top');
    });

    it('offers anyway when no keyboard opens, as when it is already open', () => {
      touchScreen();
      stubVisualViewport(500);
      fakeTimers();
      const { input } = setup(BRANCHES);

      act(() => input.focus());
      expect(screen.queryByRole('listbox')).toBeNull();
      act(() => {
        vi.advanceTimersByTime(400);
      });
      expect(screen.getByRole('listbox')).toBeInTheDocument();

      // Typing reopens it at once: the keyboard is already up.
      fireEvent.keyDown(input, { key: 'Escape' });
      expect(screen.queryByRole('listbox')).toBeNull();
      fireEvent.change(input, { target: { value: 'f' } });
      expect(screen.getByRole('listbox')).toBeInTheDocument();
    });
  });

  it('shows the value it is given', () => {
    const { input } = setup(BRANCHES, 'fix/export');
    expect(input).toHaveValue('fix/export');
  });
});
