import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { makePermissionRequest as item } from '../../test/fixtures';
import { PermissionPrompt } from './PermissionPrompt';

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

  // The dock reshapes the prompt into a band by these hooks: it hides the head
  // and the tool input, and lays the actions out across. Without them a docked
  // permission keeps its card shape.
  it('marks its parts for the surface it is drawn on', () => {
    const { container } = render(<PermissionPrompt item={item()} onDecide={vi.fn()} />);
    for (const role of ['prompt-head', 'prompt-text', 'prompt-detail', 'prompt-actions']) {
      expect(container.querySelector(`[data-role="${role}"]`)).toBeInTheDocument();
    }
  });

  it('shows the decision for an answered request', () => {
    render(<PermissionPrompt item={item({ decision: 'deny', reason: 'too risky' })} />);
    expect(screen.getByText('denied · too risky')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });
});
