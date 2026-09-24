import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { HueChip } from './HueChip';

function Icon({ className }: { className?: string }) {
  return <svg className={className} aria-hidden="true" focusable="false" />;
}

describe('HueChip', () => {
  it('names itself the same shut as open: the word is not mouse-only', () => {
    render(<HueChip href="https://x.test" icon={Icon} word="merged" tone="special" />);
    expect(screen.getByRole('link', { name: 'merged' })).toBeInTheDocument();
  });

  it('takes a fuller name than the word it shows', () => {
    render(
      <HueChip
        href="https://x.test"
        icon={Icon}
        word="merged"
        label="PR #118, merged"
        tone="special"
      />,
    );
    expect(screen.getByRole('link', { name: 'PR #118, merged' })).toBeInTheDocument();
  });

  it('says the word once: the visible copy is hidden from the tree', () => {
    render(<HueChip href="https://x.test" icon={Icon} word="merged" tone="special" />);
    // Guards against a later edit dropping `aria-hidden` and naming it
    // "merged merged".
    expect(screen.queryAllByText('merged')).toHaveLength(1);
  });

  it('carries the tone and the size the stylesheet keys off', () => {
    render(<HueChip href="https://x.test" icon={Icon} word="failed" tone="bad" size="large" />);
    const link = screen.getByRole('link', { name: 'failed' });
    // The width and the colour are the stylesheet's. What the suite can hold
    // is that the chip states which ones it asks for: `css: false` in
    // `vite.config.ts` means class names are not available here.
    expect(link).toHaveAttribute('data-tone', 'bad');
    expect(link).toHaveAttribute('data-size', 'large');
  });

  it('defaults to the dense size', () => {
    render(<HueChip href="https://x.test" icon={Icon} word="draft" tone="quiet" />);
    expect(screen.getByRole('link', { name: 'draft' })).toHaveAttribute('data-size', 'small');
  });

  it('opens its href in a new tab, safely', () => {
    render(<HueChip href="https://x.test/pull/278" icon={Icon} word="ready" tone="good" />);
    const link = screen.getByRole('link', { name: 'ready' });
    expect(link).toHaveAttribute('href', 'https://x.test/pull/278');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('does not reach a clickable parent: a chip on a node must not toggle it', () => {
    const onParentClick = vi.fn();
    render(
      <div onClick={onParentClick}>
        <HueChip href="https://x.test" icon={Icon} word="ready" tone="good" />
      </div>,
    );
    fireEvent.click(screen.getByRole('link', { name: 'ready' }));
    expect(onParentClick).not.toHaveBeenCalled();
  });

  it('reads as plain text with nowhere to go, rather than a dead link', () => {
    render(<HueChip icon={Icon} word="checking" tone="neutral" />);
    expect(screen.queryByRole('link')).toBeNull();
    // The state still reaches a screen reader without an anchor to hang on.
    expect(screen.getByLabelText('checking')).toBeInTheDocument();
  });
});

describe('HueChip where it cannot open', () => {
  it('says its word to a pointer, since a small chip never opens', () => {
    // The small chip hides its word, so without this a sighted mouse user has
    // only the colour and a 12px icon.
    render(<HueChip href="https://x.test" icon={Icon} word="ready" tone="good" />);
    expect(screen.getByRole('link', { name: 'ready' })).toHaveAttribute('title', 'ready');
  });

  it('is reachable by keyboard even with nowhere to go', () => {
    // A large chip with no href still has a word to reveal, and focus is the
    // only way to reveal it without a pointer.
    render(<HueChip icon={Icon} word="checking" tone="neutral" size="large" />);
    const chip = screen.getByLabelText('checking');
    chip.focus();
    expect(chip).toHaveFocus();
  });

  it('never ships an empty name, whatever the wire sent', () => {
    render(
      <HueChip href="https://x.test" icon={Icon} word="" tone="neutral">
        #7
      </HueChip>,
    );
    expect(screen.getByRole('link')).not.toHaveAttribute('aria-label', '');
  });
});

describe('HueChip with one mark', () => {
  it('draws a brand alone: a chip need not carry two icons', () => {
    const { container } = render(
      <HueChip href="https://x.test" brand={Icon} word="merged" tone="special">
        #118
      </HueChip>,
    );
    expect(container.querySelectorAll('svg')).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'merged' })).toBeInTheDocument();
  });
});
