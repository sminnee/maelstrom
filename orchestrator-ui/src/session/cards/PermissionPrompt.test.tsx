import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { makePermissionRequest as item } from '../../fake/fixtures';
import { PermissionPrompt } from './PermissionPrompt';

describe('PermissionPrompt', () => {
  it('shows the decide row while the request is open', () => {
    render(<PermissionPrompt item={item()} onDecide={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('offers remember only when the agent sent a label', () => {
    const { rerender } = render(<PermissionPrompt item={item()} onDecide={vi.fn()} />);
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Approve']);
    rerender(
      <PermissionPrompt item={item({ remember: 'Allow edits this session' })} onDecide={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: 'Allow edits this session' })).toBeInTheDocument();
  });

  it('says a remembered allow was remembered', () => {
    render(<PermissionPrompt item={item({ decision: 'allow', remembered: true })} />);
    expect(screen.getByText('allowed · remembered')).toBeInTheDocument();
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
