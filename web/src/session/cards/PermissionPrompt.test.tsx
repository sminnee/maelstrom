import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { PermissionRequestItem } from '../../protocol/transcript';
import { PermissionPrompt } from './PermissionPrompt';

function item(over: Partial<PermissionRequestItem> = {}): PermissionRequestItem {
  return {
    id: 'p1',
    ts: '',
    type: 'permission_request',
    requestId: 'req-1',
    tool: 'Write',
    input: { file_path: '/tmp/hello.txt' },
    description: 'Write hello.txt',
    ...over,
  };
}

describe('PermissionPrompt', () => {
  it('offers Approve and Deny while the request is open', () => {
    render(<PermissionPrompt item={item()} onDecide={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument();
  });

  it('offers nothing for a request nothing answered, even with a handler', () => {
    render(<PermissionPrompt item={item({ stale: true })} onDecide={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Deny' })).toBeNull();
    expect(screen.getByText('no longer pending')).toBeInTheDocument();
  });

  it('shows the decision for an answered request', () => {
    render(<PermissionPrompt item={item({ decision: 'deny', reason: 'too risky' })} />);
    expect(screen.getByText('denied · too risky')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });
});
