import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Document } from '../protocol/documents';
import { makeDocument } from '../test/fixtures';
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

describe('ReviewActions', () => {
  it('offers Approve while the plan awaits review', () => {
    bar('awaiting-review');
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('offers nothing once the plan has gone stale, and says why', () => {
    bar('stale');
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Request changes' })).toBeNull();
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
        doc={makeDocument({ kind: 'tasks', source: { type: 'draft_files', paths: ['d.md'] } })}
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
});
