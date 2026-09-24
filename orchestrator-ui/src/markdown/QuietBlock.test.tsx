import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Markdown } from './Markdown';

const withLink =
  '<user-attention low>\nChecking the allocator first. Then [read the doc](/docs) for the rest.';

/**
 * jsdom computes no layout, so `useClamped`'s real scrollHeight/clientHeight
 * read 0/0 and report unclamped whatever the content. Mocking the prototype
 * getters before render is how `ComboBox.test.tsx` gets a measured case too —
 * `useClamped` measures in a `useEffect` on mount, so the mock must be in
 * place before that runs.
 */
function mockClamped() {
  vi.spyOn(HTMLDivElement.prototype, 'scrollHeight', 'get').mockReturnValue(100);
  vi.spyOn(HTMLDivElement.prototype, 'clientHeight', 'get').mockReturnValue(40);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('QuietBlock', () => {
  it('offers no control when nothing is clamped', () => {
    render(<Markdown source={'<user-attention low>\nOne short line.'} />);
    expect(screen.queryByRole('button', { name: /show more/i })).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('keeps the full text in the accessibility tree while collapsed', () => {
    mockClamped();
    render(<Markdown source={withLink} />);
    expect(screen.getByText(/Checking the allocator first/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'read the doc' })).toBeInTheDocument();
  });

  it('a click on a link inside the block does not expand it', async () => {
    mockClamped();
    const user = userEvent.setup();
    render(<Markdown source={withLink} />);
    await user.click(screen.getByRole('link', { name: 'read the doc' }));
    expect(screen.queryByRole('button', { name: /show less/i })).toBeNull();
  });

  it('a click on the clamped text itself expands it', async () => {
    mockClamped();
    const user = userEvent.setup();
    render(<Markdown source={withLink} />);
    await user.click(screen.getByText(/Checking the allocator first/));
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();
  });

  it('the collapsed body is keyboard-focusable and Enter expands it, revealing a Show less link', async () => {
    mockClamped();
    const user = userEvent.setup();
    render(<Markdown source={withLink} />);

    const body = screen.getByRole('button', { name: /Checking the allocator first/ });
    expect(body).toHaveAttribute('aria-expanded', 'false');

    await user.tab(); // the body wraps the link, so it is first in tab order
    expect(body).toHaveFocus();
    await user.keyboard('{Enter}');

    const showLess = screen.getByRole('button', { name: 'Show less' });
    expect(showLess).toHaveAttribute('aria-controls', body.id);
    expect(body).not.toHaveAttribute('role');

    await user.click(showLess);
    expect(screen.queryByRole('button', { name: 'Show less' })).toBeNull();
  });

  it('a Space key on the focused collapsed body also expands it', async () => {
    mockClamped();
    const user = userEvent.setup();
    render(<Markdown source={withLink} />);

    const body = screen.getByRole('button', { name: /Checking the allocator first/ });
    await user.tab();
    expect(body).toHaveFocus();
    await user.keyboard(' ');

    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();
  });
});
