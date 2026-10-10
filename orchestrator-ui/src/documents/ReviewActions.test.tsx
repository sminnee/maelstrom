import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import type { Document } from '../protocol/documents';
import { makeDocument } from '../fake/fixtures';
import { ReviewActions } from './ReviewActions';

function bar(
  status: Document['status'],
  props: Partial<ComponentProps<typeof ReviewActions>> = {},
) {
  const { container } = render(
    <ReviewActions
      doc={makeDocument({ status })}
      unresolved={0}
      onApprove={vi.fn()}
      onRequestChanges={vi.fn()}
      {...props}
    />,
  );
  return container;
}

const field = () => screen.getByRole('textbox', { name: 'Summary of requested changes' });

function precedes(a: Element, b: Element) {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe('ReviewActions', () => {
  it('offers Approve while the plan awaits review', () => {
    bar('awaiting-review');
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  // DOM order is the tab order, and DESIGN.md wants it to match what the eye reads.
  it('puts the field first, with the one button under it', () => {
    bar('awaiting-review');
    const approve = screen.getByRole('button', { name: 'Approve' });
    expect(precedes(field(), approve)).toBe(true);
    expect(approve.parentElement).not.toContainElement(field());
    expect(screen.queryByRole('button', { name: 'Decline' })).toBeNull();
  });

  it('turns Approve into Decline while the field holds a summary, and sends it', async () => {
    const onApprove = vi.fn();
    const onRequestChanges = vi.fn();
    bar('awaiting-review', { onApprove, onRequestChanges });

    await userEvent.type(field(), '   ');
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();

    await userEvent.type(field(), 'split step 2  ');
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Decline' }));

    expect(onRequestChanges).toHaveBeenCalledWith('split step 2');
    expect(onApprove).not.toHaveBeenCalled();
  });

  it('declines with no summary while a comment is unresolved, and never approves', async () => {
    const onApprove = vi.fn();
    const onRequestChanges = vi.fn();
    bar('awaiting-review', { unresolved: 2, onApprove, onRequestChanges });

    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Decline' }));

    expect(onRequestChanges).toHaveBeenCalledWith('');
    expect(onApprove).not.toHaveBeenCalled();
  });

  // The two acts are two buttons, so a failed Approve does not show its error
  // on the Decline that replaces it.
  it('keeps a failed Approve off the Decline that replaces it', async () => {
    bar('awaiting-review', { onApprove: () => Promise.reject(new Error('gone')) });

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await screen.findByRole('alert');
    await userEvent.type(field(), 'split step 2');

    expect(screen.getByRole('button', { name: 'Decline' })).toBeEnabled();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('offers nothing once the plan has gone stale, and says why', () => {
    bar('stale');
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Decline' })).toBeNull();
    expect(screen.getByText('This version is stale.')).toBeInTheDocument();
  });

  it('says an approved version is approved', () => {
    bar('approved');
    expect(screen.getByText('This version is approved.')).toBeInTheDocument();
  });

  it('draws no bar at all on a draft', () => {
    expect(bar('draft')).toBeEmptyDOMElement();
  });

  it('names what approving a task set does, because it writes to the notebook', () => {
    bar('awaiting-review', {
      doc: makeDocument({
        kind: 'tasks',
        source: { type: 'draft_file', fileId: 'f-d.md', filename: 'd.md' },
      }),
    });
    expect(screen.getByRole('button', { name: 'Approve and create tasks' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });

  it('keeps the plain label on every other kind', () => {
    bar('awaiting-review', { doc: makeDocument({ kind: 'other' }) });
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('counts the group, because one verdict settles every member', () => {
    bar('awaiting-review', { doc: makeDocument({ kind: 'other' }), members: 3 });
    expect(screen.getByRole('button', { name: 'Approve all 3' })).toBeInTheDocument();
  });

  it('counts the group on Decline too', () => {
    bar('awaiting-review', { doc: makeDocument({ kind: 'other' }), members: 3, unresolved: 1 });
    expect(screen.getByRole('button', { name: 'Decline all 3' })).toBeInTheDocument();
  });

  it('says a settled group is settled as a whole', () => {
    bar('approved', { doc: makeDocument({ kind: 'other', status: 'approved' }), members: 3 });
    expect(screen.getByText('All 3 are approved.')).toBeInTheDocument();
  });
});
