import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DecideRow } from './DecideRow';

describe('DecideRow', () => {
  it('withholds Deny until there is a reason to give', async () => {
    render(<DecideRow onDecide={vi.fn()} />);
    const deny = screen.getByRole('button', { name: 'Deny' });
    expect(deny).toBeDisabled();

    await userEvent.type(screen.getByRole('textbox', { name: 'Deny reason' }), 'too risky');
    expect(deny).toBeEnabled();
  });

  it('sends the reason trimmed', async () => {
    const onDecide = vi.fn();
    render(<DecideRow onDecide={onDecide} />);

    await userEvent.type(screen.getByRole('textbox', { name: 'Deny reason' }), '  too risky  ');
    await userEvent.click(screen.getByRole('button', { name: 'Deny' }));

    expect(onDecide).toHaveBeenCalledWith('deny', 'too risky');
  });

  it('counts whitespace as no reason', async () => {
    render(<DecideRow onDecide={vi.fn()} />);

    await userEvent.type(screen.getByRole('textbox', { name: 'Deny reason' }), '   ');

    expect(screen.getByRole('button', { name: 'Deny' })).toBeDisabled();
  });

  it('approves with no reason, whatever the field holds', async () => {
    const onDecide = vi.fn();
    render(<DecideRow onDecide={onDecide} />);

    await userEvent.type(screen.getByRole('textbox', { name: 'Deny reason' }), 'ignored');
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));

    expect(onDecide).toHaveBeenCalledWith('approve', '');
  });

  it('offers both acts but takes neither with no handler', () => {
    render(<DecideRow />);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Deny' })).toBeDisabled();
  });

  // The dock lays a band out by this hook, from a stylesheet in another
  // directory. Renaming it here would silently return both docked routes to
  // their card shape.
  it('marks the row for the surface it is drawn on', () => {
    const { container } = render(<DecideRow onDecide={vi.fn()} />);
    expect(container.querySelector('[data-role="prompt-actions"]')).toBeInTheDocument();
  });
});
