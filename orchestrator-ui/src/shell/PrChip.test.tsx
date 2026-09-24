import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PrChip } from './PrChip';
import { makeWorktree } from '../test/fixtures';

const withPr = (over: Parameters<typeof makeWorktree>[0] = {}) =>
  makeWorktree({
    prNumber: 118,
    prUrl: 'https://github.com/acme/northwind/pull/118',
    prState: 'ready',
    ...over,
  });

describe('PrChip', () => {
  it('draws nothing where there is no PR', () => {
    const { container } = render(<PrChip worktree={makeWorktree()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('draws nothing without a worktree at all', () => {
    const { container } = render(<PrChip />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the PR and its state, so colour is never the only channel', () => {
    render(<PrChip worktree={withPr()} />);
    expect(screen.getByRole('link', { name: 'PR #118, ready to merge' })).toBeInTheDocument();
  });

  it('opens the PR it names', () => {
    render(<PrChip worktree={withPr()} />);
    expect(screen.getByRole('link', { name: /PR #118/ })).toHaveAttribute(
      'href',
      'https://github.com/acme/northwind/pull/118',
    );
  });

  it('parts merged from ready, which the old dot could not', () => {
    // One render each: two in the same container cannot say which chip is
    // which, so the assertion would hold even if the two tones swapped.
    const merged = render(<PrChip worktree={withPr({ prState: 'merged' })} />);
    expect(screen.getByRole('link', { name: /PR #118/ })).toHaveAttribute('data-tone', 'special');
    merged.unmount();
    render(<PrChip worktree={withPr({ prState: 'ready' })} />);
    expect(screen.getByRole('link', { name: /PR #118/ })).toHaveAttribute('data-tone', 'good');
  });

  it('reads a draft as a draft, whatever its checks are doing', () => {
    render(<PrChip worktree={withPr({ prState: 'ci-failed', prDraft: true })} />);
    const link = screen.getByRole('link', { name: 'PR #118, draft' });
    expect(link).toHaveAttribute('data-tone', 'quiet');
  });

  it('is text, not a dead link, when the repo has no browse URL', () => {
    render(<PrChip worktree={withPr({ prUrl: '' })} />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByLabelText('PR #118, ready to merge')).toBeInTheDocument();
  });

  it('opens to say its state only where there is room to open', () => {
    render(<PrChip worktree={withPr()} size="large" />);
    expect(screen.getByRole('link', { name: /PR #118/ })).toHaveAttribute('data-size', 'large');
  });
});

describe('PrChip inside a clickable row', () => {
  it('drops its link where the row is already a button: an anchor may not nest in one', () => {
    // `DeckRow` wraps its meta line in a `<button>`, and interactive content
    // inside a button is invalid HTML with undefined focus behaviour.
    render(<PrChip worktree={withPr()} link={false} />);
    expect(screen.queryByRole('link')).toBeNull();
    // The reading survives: only the anchor goes.
    expect(screen.getByLabelText('PR #118, ready to merge')).toHaveAttribute('data-tone', 'good');
  });
});
