import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DecideRow } from './DecideRow';

function precedes(a: Element, b: Element) {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

function parts() {
  return [
    screen.getByRole('button', { name: 'Approve' }),
    screen.getByRole('textbox', { name: 'Deny reason' }),
    screen.getByRole('button', { name: 'Deny' }),
  ] as const;
}

describe('DecideRow', () => {
  // DOM order is the tab order, and DESIGN.md wants it to match what the eye reads.
  it('puts the field first, with both buttons in a row under it, Approve leading', () => {
    render(<DecideRow onDecide={vi.fn()} />);
    const [approve, field, deny] = parts();
    expect(precedes(field, approve)).toBe(true);
    expect(precedes(approve, deny)).toBe(true);
    expect(approve.parentElement).toContainElement(deny);
    expect(approve.parentElement).not.toContainElement(field);
  });

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

  it('keeps the line breaks of a reason over several lines', async () => {
    const onDecide = vi.fn();
    render(<DecideRow onDecide={onDecide} />);

    await userEvent.type(
      screen.getByRole('textbox', { name: 'Deny reason' }),
      'too risky{Enter}try a dry run',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Deny' }));

    expect(onDecide).toHaveBeenCalledWith('deny', 'too risky\ntry a dry run');
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
