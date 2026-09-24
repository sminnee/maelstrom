import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
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

/** The open listbox's rows, in the order they are offered. */
function rows() {
  return within(screen.getByRole('listbox'))
    .getAllByRole('option')
    .map((o) => o.textContent);
}

describe('ComboBox', () => {
  // The placement tests spy on `innerHeight` and `scrollHeight`. `restoreMocks`
  // is not set, so without this they would leak into the tests that follow.
  afterEach(() => vi.restoreAllMocks());

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
    input.getBoundingClientRect = () =>
      ({ top: 200, bottom: 226, left: 0, right: 100, width: 100, height: 26 }) as DOMRect;

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
    input.getBoundingClientRect = () =>
      ({ top: 200, bottom: 226, left: 0, right: 100, width: 100, height: 26 }) as DOMRect;

    await user.click(input);
    expect(screen.getByRole('listbox').dataset.position).toBe('top');

    // One row now, which fits in the 72px below the field.
    height.mockReturnValue(30);
    await user.type(input, 'log');
    expect(screen.getByRole('listbox').dataset.position).toBe('bottom');
  });

  it('shows the value it is given', () => {
    const { input } = setup(BRANCHES, 'fix/export');
    expect(input).toHaveValue('fix/export');
  });
});
