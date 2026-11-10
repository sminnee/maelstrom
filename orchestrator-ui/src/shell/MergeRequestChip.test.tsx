import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MergeRequestChip } from './MergeRequestChip';
import type { WorktreeMergeRequest } from '../protocol/entities';

const mr = (over: Partial<WorktreeMergeRequest> = {}): WorktreeMergeRequest => ({
  id: 'mr3',
  status: 'passed',
  sha: 'a'.repeat(40),
  detail: '',
  gate: null,
  ...over,
});

describe('MergeRequestChip', () => {
  it('draws nothing where the branch has no Merge request', () => {
    const { container } = render(<MergeRequestChip mr={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the request and its status, in the status colour', () => {
    render(<MergeRequestChip mr={mr({ status: 'failed' })} />);
    const chip = screen.getByLabelText('Merge request mr3, failed');
    expect(chip).toHaveAttribute('data-tone', 'bad');
    expect(chip.closest('[data-state]')).toHaveAttribute('data-state', 'failed');
  });

  it('shows the running Build’s gate lozenge while it builds', () => {
    const gate = { jobId: '7', state: 'running' as const, gate: 'test', queuePosition: null };
    render(<MergeRequestChip mr={mr({ status: 'building', gate })} interactive={false} />);
    expect(screen.getByLabelText('gate job 7: test running')).toBeInTheDocument();
  });

  it('drops the lozenge once the Build has ended', () => {
    const gate = { jobId: '7', state: 'running' as const, gate: 'test', queuePosition: null };
    render(<MergeRequestChip mr={mr({ status: 'passed', gate })} interactive={false} />);
    expect(screen.queryByLabelText(/gate job 7/)).not.toBeInTheDocument();
  });
});
