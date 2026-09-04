import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Document } from '../protocol/documents';
import { makeDocument } from '../test/fixtures';
import { ReviewActions } from './ReviewActions';

function bar(status: Document['status']) {
  render(
    <ReviewActions
      doc={makeDocument({ status })}
      unresolved={0}
      onApprove={vi.fn()}
      onRequestChanges={vi.fn()}
    />,
  );
}

describe('ReviewActions', () => {
  it('offers Approve while the plan awaits review', () => {
    bar('awaiting-review');
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('offers nothing once the plan has gone stale', () => {
    bar('stale');
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Request changes' })).toBeNull();
    expect(screen.getByText('This version is stale.')).toBeInTheDocument();
  });
});
