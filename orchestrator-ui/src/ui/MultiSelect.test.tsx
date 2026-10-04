import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { MultiSelect } from './MultiSelect';

const OPTIONS = [
  { value: 'todo', label: 'todo' },
  { value: 'done', label: 'done' },
  { value: 'cancelled', label: 'cancelled' },
];

/** The control as a caller holds it: the value is the caller's state. */
function Held({ initial }: { initial: string[] }) {
  const [value, setValue] = useState<readonly string[]>(initial);
  return <MultiSelect label="Status" options={OPTIONS} value={value} onChange={setValue} />;
}

const trigger = () => screen.getByRole('button', { name: /^Status/ });
const item = (name: string) => screen.getByRole('menuitemcheckbox', { name });

describe('MultiSelect', () => {
  it("shows the picked labels on its trigger, in the options' order, under its label", async () => {
    const user = userEvent.setup();
    render(<Held initial={['cancelled']} />);
    expect(trigger()).toHaveAccessibleName('Status cancelled');
    await user.click(trigger());
    await user.click(item('todo'));
    expect(trigger()).toHaveTextContent(/^todo, cancelled$/);
  });

  it('toggles an item on click, and the menu stays open', async () => {
    const user = userEvent.setup();
    render(<Held initial={['todo']} />);
    await user.click(trigger());
    expect(item('done')).toHaveAttribute('aria-checked', 'false');

    await user.click(item('done'));
    expect(item('done')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('menu', { name: /^Status/ })).toBeVisible();
    expect(trigger()).toHaveTextContent(/^todo, done$/);

    await user.click(item('todo'));
    expect(item('todo')).toHaveAttribute('aria-checked', 'false');
    expect(trigger()).toHaveTextContent(/^done$/);
  });

  it('moves the focus between check items with the arrow keys, Home and End', async () => {
    const user = userEvent.setup();
    render(<Held initial={[]} />);
    await user.click(trigger());
    await user.keyboard('{ArrowDown}');
    expect(item('done')).toHaveFocus();
    await user.keyboard('{End}');
    expect(item('cancelled')).toHaveFocus();
    await user.keyboard('{Home}');
    expect(item('todo')).toHaveFocus();
  });

  it('closes on Escape and gives the focus back to the trigger', async () => {
    const user = userEvent.setup();
    render(<Held initial={[]} />);
    expect(trigger()).toHaveTextContent(/^none$/);
    await user.click(trigger());
    expect(item('todo')).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu', { name: /^Status/ })).toBeNull();
    expect(trigger()).toHaveFocus();
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });
});
