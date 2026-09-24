import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ImageLightbox } from './ImageLightbox';

describe('a picture in a message', () => {
  /** Open the overlay from the thumbnail. */
  async function open(user: ReturnType<typeof userEvent.setup>) {
    render(<ImageLightbox src="/shot.png" alt="the failing header" />);
    await user.click(screen.getByRole('button', { name: 'the failing header — open full size' }));
    return screen.getByTestId('image-lightbox');
  }

  it('opens the full picture from its thumbnail', async () => {
    const user = userEvent.setup();
    const box = await open(user);
    expect(within(box).getByRole('img', { name: 'the failing header' })).toBeInTheDocument();
  });

  it('closes on its own control, which is the only way out on a touch screen', async () => {
    const user = userEvent.setup();
    const box = await open(user);
    // `Dialog` dismisses a press outside the box, and below 839px the box is the
    // whole screen. Escape is no answer on a phone, so the overlay carries a
    // control of its own.
    await user.click(within(box).getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('image-lightbox')).toBeNull();
  });
});
