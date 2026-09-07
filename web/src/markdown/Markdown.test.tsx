import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Markdown } from './Markdown';

const SHOT = '![The failing dialog](/api/files/ag1-2-shot.png)';

describe('an image in a message', () => {
  it('renders as a thumbnail carrying its alt text', () => {
    render(<Markdown source={SHOT} />);
    const image = screen.getByAltText('The failing dialog');
    expect(image).toHaveAttribute('src', '/api/files/ag1-2-shot.png');
  });

  it('opens the full image in a lightbox when clicked', async () => {
    const user = userEvent.setup();
    render(<Markdown source={SHOT} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /The failing dialog/ }));

    const lightbox = screen.getByRole('dialog');
    expect(lightbox).toBeInTheDocument();
    // The full image, not a second copy of the thumbnail.
    expect(within(lightbox).getByAltText('The failing dialog')).toHaveAttribute(
      'src',
      '/api/files/ag1-2-shot.png',
    );
  });

  it('closes the lightbox on Escape', async () => {
    const user = userEvent.setup();
    render(<Markdown source={SHOT} />);
    await user.click(screen.getByRole('button', { name: /The failing dialog/ }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('is reachable by keyboard, so the picture is not mouse-only', async () => {
    const user = userEvent.setup();
    render(<Markdown source={SHOT} />);
    await user.tab();
    expect(screen.getByRole('button', { name: /The failing dialog/ })).toHaveFocus();
  });

  it('names the button and the lightbox even when the image has no alt text', async () => {
    // `![](/x.png)` is legal markdown, and the component cannot rely on a
    // producer it does not own to fill the alt in.
    const user = userEvent.setup();
    render(<Markdown source={'![](/api/files/ag1-2-shot.png)'} />);

    const button = screen.getByRole('button');
    expect(button).toHaveAccessibleName('Open image full size');

    await user.click(button);
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Image');
  });

  it('returns focus to the thumbnail when the lightbox closes', async () => {
    // Without this a keyboard user loses their place in the transcript, and
    // the next Tab restarts from the top of the document.
    const user = userEvent.setup();
    render(<Markdown source={SHOT} />);
    const button = screen.getByRole('button', { name: /The failing dialog/ });

    await user.click(button);
    await user.keyboard('{Escape}');

    expect(button).toHaveFocus();
  });

  it('leaves ordinary prose alone', () => {
    render(<Markdown source={'A **bold** claim.'} />);
    expect(screen.getByText('bold')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
