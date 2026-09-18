import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { type ComboOption } from './ComboBox';
import { MultiComboBox } from './MultiComboBox';

const TASKS: ComboOption[] = [
  { value: 'NORT-9', label: 'Migrate to Postgres 16' },
  { value: 'NORT-12', label: 'Rotate auth tokens' },
  { value: 'NORT-20', label: 'Add a Linear kind' },
];

/**
 * Render a MultiComboBox as the app holds it: controlled, its value fed back
 * in. `onChange` reports what it was asked to change to.
 */
function setup(options: ComboOption[], initial: string[] = [], readOnly = false) {
  const onChange = vi.fn();
  function Harness() {
    const [value, setValue] = useState(initial);
    return (
      <div>
        <label htmlFor="follows">Follows</label>
        <MultiComboBox
          id="follows"
          value={value}
          options={options}
          readOnly={readOnly}
          onChange={(v) => {
            onChange(v);
            setValue(v);
          }}
        />
      </div>
    );
  }
  render(<Harness />);
  return { onChange, input: screen.getByLabelText('Follows') };
}

/** The open listbox's rows, in the order they are offered. */
function rows() {
  return within(screen.getByRole('listbox'))
    .getAllByRole('option')
    .map((o) => o.textContent);
}

/** The chips currently shown, by their visible text. */
function chips() {
  return screen.getAllByRole('listitem').map((li) => li.textContent?.replace('×', ''));
}

describe('MultiComboBox', () => {
  it('offers every option once opened, when nothing is selected yet', async () => {
    const user = userEvent.setup();
    const { input } = setup(TASKS);
    await user.click(input);
    expect(rows()).toEqual([
      'NORT-9Migrate to Postgres 16',
      'NORT-12Rotate auth tokens',
      'NORT-20Add a Linear kind',
    ]);
  });

  it('narrows the offer to what was typed, matching the value or the label', async () => {
    const user = userEvent.setup();
    const { input } = setup(TASKS);
    await user.type(input, 'auth');
    expect(rows()).toEqual(['NORT-12Rotate auth tokens']);
  });

  it('excludes an option already selected from the offer', async () => {
    const user = userEvent.setup();
    const { input } = setup(TASKS, ['NORT-9']);
    await user.click(input);
    expect(rows()).toEqual(['NORT-12Rotate auth tokens', 'NORT-20Add a Linear kind']);
  });

  it('shows the selected options as chips', () => {
    setup(TASKS, ['NORT-9', 'NORT-12']);
    expect(chips()).toEqual(['Migrate to Postgres 16', 'Rotate auth tokens']);
  });

  it('choosing a row adds it and keeps the offer open for another pick', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(TASKS, ['NORT-9']);
    await user.click(input);
    await user.click(screen.getByRole('option', { name: /Rotate auth tokens/ }));

    expect(onChange).toHaveBeenLastCalledWith(['NORT-9', 'NORT-12']);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(input).toHaveValue('');
  });

  it('removing a chip drops it from the value', async () => {
    const user = userEvent.setup();
    const { onChange } = setup(TASKS, ['NORT-9', 'NORT-12']);
    await user.click(screen.getByRole('button', { name: 'Remove Migrate to Postgres 16' }));
    expect(onChange).toHaveBeenLastCalledWith(['NORT-12']);
  });

  it('never submits the typed filter text as a value', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(TASKS);
    await user.type(input, 'nothing matches this');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('offers nothing once every option is already selected', async () => {
    const user = userEvent.setup();
    const { input } = setup(
      TASKS,
      TASKS.map((t) => t.value),
    );
    await user.click(input);
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('shows chips without a remove control when read-only, and never opens the offer', async () => {
    const user = userEvent.setup();
    const { input } = setup(TASKS, ['NORT-9'], true);
    expect(screen.getByText('Migrate to Postgres 16')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Remove/ })).toBeNull();
    await user.click(input);
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('walks the offer with the arrow keys and takes one with Enter', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(TASKS);
    await user.click(input);
    await user.keyboard('{ArrowDown}{ArrowDown}');
    const active = screen.getByRole('option', { name: /Rotate auth tokens/ });
    expect(input).toHaveAttribute('aria-activedescendant', active.id);
    await user.keyboard('{Enter}');
    expect(onChange).toHaveBeenLastCalledWith(['NORT-12']);
    // Choosing keeps the offer open, unlike `ComboBox`.
    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('closes on Escape without choosing anything', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(TASKS);
    await user.click(input);
    await user.keyboard('{ArrowDown}{Escape}');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('closes when the focus leaves it', async () => {
    const user = userEvent.setup();
    const { input } = setup(TASKS);
    await user.click(input);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    await user.tab();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('removes the last chip on Backspace over an empty filter', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(TASKS, ['NORT-9', 'NORT-12']);
    await user.click(input);
    await user.keyboard('{Backspace}');
    expect(onChange).toHaveBeenLastCalledWith(['NORT-9']);
  });

  it('never removes a chip on Backspace when read-only', async () => {
    const user = userEvent.setup();
    const { onChange, input } = setup(TASKS, ['NORT-9', 'NORT-12'], true);
    await user.click(input);
    await user.keyboard('{Backspace}');
    expect(onChange).not.toHaveBeenCalled();
  });
});
