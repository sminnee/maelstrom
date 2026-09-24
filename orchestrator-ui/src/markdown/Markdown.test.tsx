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

describe('user attention in a message', () => {
  it('renders untagged prose at the reading rank', () => {
    render(<Markdown source={'Working commentary.'} />);
    expect(screen.queryByTestId('quiet')).toBeNull();
    expect(screen.getByText('Working commentary.')).toBeInTheDocument();
  });

  it('keeps prose before the first tag at the reading rank', () => {
    render(<Markdown source={'Answer first.\n\n<user-attention low>\nWorking detail.'} />);
    const quiet = screen.getByTestId('quiet');
    expect(screen.getByText('Answer first.')).toBeInTheDocument();
    expect(quiet).toHaveTextContent('Working detail.');
    expect(quiet).not.toHaveTextContent('Answer first.');
  });

  it('renders low-ranked markdown as prose', () => {
    render(
      <Markdown source={'<user-attention low>\nRun `mael env reset` or [read the doc](/docs).'} />,
    );
    const quiet = screen.getByTestId('quiet');
    expect(within(quiet).getByText('mael env reset').tagName).toBe('CODE');
    expect(within(quiet).getByRole('link', { name: 'read the doc' })).toHaveAttribute(
      'href',
      '/docs',
    );
  });

  it('switches back to the reading rank with a second tag', () => {
    render(
      <Markdown source={'<user-attention low>\nChecking.\n\n<user-attention high>\nFinished.'} />,
    );
    expect(screen.getByTestId('quiet')).toHaveTextContent('Checking.');
    expect(screen.getByText('Finished.')).toBeInTheDocument();
  });

  it('treats an unknown attention value as high', () => {
    render(<Markdown source={'<user-attention medium>\nRead this.'} />);
    expect(screen.queryByTestId('quiet')).toBeNull();
    expect(screen.getByText('Read this.')).toBeInTheDocument();
  });

  it('leaves a marker in a fenced listing literal', () => {
    const source = '````text\nouter\n\n<user-attention low>\ninner\n````';
    render(<Markdown source={source} />);
    expect(screen.queryByTestId('quiet')).toBeNull();
    expect(screen.getByText(/user-attention low/)).toBeInTheDocument();
  });

  it('leaves a marker mid-paragraph literal', () => {
    render(<Markdown source={'Answer <user-attention low> working detail.'} />);
    expect(screen.queryByTestId('quiet')).toBeNull();
    expect(screen.getByText(/user-attention low/)).toBeInTheDocument();
  });

  it('leaves the retired quiet fence as a code block', () => {
    // This override sits in front of every code block an agent writes, so the
    // ordinary case must survive it untouched.
    const { container } = render(<Markdown source={'```quiet\nChecking port 342.\n```'} />);
    expect(screen.queryByTestId('quiet')).toBeNull();
    expect(container.querySelector('pre')).not.toBeNull();
    expect(screen.getByText(/Checking port 342/)).toBeInTheDocument();
  });

  it('leaves a fence with no info string as a code block', () => {
    const { container } = render(<Markdown source={'```\nplain listing\n```'} />);
    expect(screen.queryByTestId('quiet')).toBeNull();
    expect(container.querySelector('pre')).not.toBeNull();
    expect(screen.getByText(/plain listing/)).toBeInTheDocument();
  });
});
