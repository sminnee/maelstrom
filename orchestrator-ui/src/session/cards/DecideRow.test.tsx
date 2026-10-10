import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DecideRow } from './DecideRow';

function precedes(a: Element, b: Element) {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

const field = () => screen.getByRole('textbox', { name: 'Deny reason' });

describe('DecideRow', () => {
  // DOM order is the tab order, and DESIGN.md wants it to match what the eye reads.
  it('puts the field first, with the one button under it', () => {
    render(<DecideRow onDecide={vi.fn()} />);
    const approve = screen.getByRole('button', { name: 'Approve' });
    expect(precedes(field(), approve)).toBe(true);
    expect(approve.parentElement).not.toContainElement(field());
  });

  it('turns Approve into Deny while the field holds a comment, and back', async () => {
    render(<DecideRow onDecide={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Deny' })).toBeNull();

    await userEvent.type(field(), 'too risky');
    expect(screen.getByRole('button', { name: 'Deny' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();

    await userEvent.clear(field());
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Deny' })).toBeNull();
  });

  it('counts whitespace as no comment', async () => {
    const onDecide = vi.fn();
    render(<DecideRow onDecide={onDecide} />);

    await userEvent.type(field(), '   ');
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));

    expect(onDecide).toHaveBeenCalledWith('approve', '');
  });

  it('sends the reason trimmed', async () => {
    const onDecide = vi.fn();
    render(<DecideRow onDecide={onDecide} />);

    await userEvent.type(field(), '  too risky  ');
    await userEvent.click(screen.getByRole('button', { name: 'Deny' }));

    expect(onDecide).toHaveBeenCalledWith('deny', 'too risky');
  });

  it('keeps the line breaks of a reason over several lines', async () => {
    const onDecide = vi.fn();
    render(<DecideRow onDecide={onDecide} />);

    await userEvent.type(field(), 'too risky{Enter}try a dry run');
    await userEvent.click(screen.getByRole('button', { name: 'Deny' }));

    expect(onDecide).toHaveBeenCalledWith('deny', 'too risky\ntry a dry run');
  });

  it('takes no act with no handler', async () => {
    render(<DecideRow />);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();

    await userEvent.type(field(), 'too risky');
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
