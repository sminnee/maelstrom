import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ExternalLink } from './ExternalLink';

describe('ExternalLink', () => {
  it('opens its href in a new tab, safely', () => {
    render(<ExternalLink href="https://github.com/acme/northwind/pull/278">PR #278</ExternalLink>);
    const link = screen.getByRole('link', { name: 'PR #278' });
    expect(link).toHaveAttribute('href', 'https://github.com/acme/northwind/pull/278');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('does not reach a clickable parent: a link on a node must not toggle the node', () => {
    const onParentClick = vi.fn();
    render(
      <div onClick={onParentClick}>
        <ExternalLink href="https://example.test/app">Dev env</ExternalLink>
      </div>,
    );
    fireEvent.click(screen.getByRole('link', { name: 'Dev env' }));
    expect(onParentClick).not.toHaveBeenCalled();
  });
});
