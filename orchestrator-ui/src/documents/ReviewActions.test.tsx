import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Document } from '../protocol/documents';
import { makeDocument } from '../fake/fixtures';
import { ReviewActions } from './ReviewActions';

function bar(status: Document['status']) {
  const { container } = render(
    <ReviewActions
      doc={makeDocument({ status })}
      unresolved={0}
      onApprove={vi.fn()}
      onRequestChanges={vi.fn()}
    />,
  );
  return container;
}

function precedes(a: Element, b: Element) {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe('ReviewActions', () => {
  it('offers Approve while the plan awaits review', () => {
    bar('awaiting-review');
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  // DOM order is the tab order, and DESIGN.md wants it to match what the eye reads.
  it('puts the field first, with both buttons in a row under it, Approve leading', () => {
    bar('awaiting-review');
    const [approve, field, decline] = [
      screen.getByRole('button', { name: 'Approve' }),
      screen.getByRole('textbox', { name: 'Summary of requested changes' }),
      screen.getByRole('button', { name: 'Decline' }),
    ];
    expect(precedes(field, approve)).toBe(true);
    expect(precedes(approve, decline)).toBe(true);
    expect(approve.parentElement).toContainElement(decline);
    expect(approve.parentElement).not.toContainElement(field);
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
    render(
      <ReviewActions
        doc={makeDocument({
          kind: 'tasks',
          source: { type: 'draft_file', fileId: 'f-d.md', filename: 'd.md' },
        })}
        unresolved={0}
        onApprove={vi.fn()}
        onRequestChanges={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Approve and create tasks' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });

  it('keeps the plain label on every other kind', () => {
    render(
      <ReviewActions
        doc={makeDocument({ kind: 'other' })}
        unresolved={0}
        onApprove={vi.fn()}
        onRequestChanges={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('counts the group, because one verdict settles every member', () => {
    render(
      <ReviewActions
        doc={makeDocument({ kind: 'other' })}
        members={3}
        unresolved={0}
        onApprove={vi.fn()}
        onRequestChanges={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Approve all 3' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decline all 3' })).toBeInTheDocument();
  });

  it('says a settled group is settled as a whole', () => {
    render(
      <ReviewActions
        doc={makeDocument({ kind: 'other', status: 'approved' })}
        members={3}
        unresolved={0}
        onApprove={vi.fn()}
        onRequestChanges={vi.fn()}
      />,
    );
    expect(screen.getByText('All 3 are approved.')).toBeInTheDocument();
  });
});
