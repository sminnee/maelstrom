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

describe('a callout in a message', () => {
  it('renders its text as prose, not as a code block', () => {
    const { container } = render(
      <Markdown source={'```callout\nYou will need to free 342.\n```'} />,
    );
    const callout = screen.getByTestId('callout');
    expect(callout).toHaveTextContent('You will need to free 342.');
    // The fence chrome is what a callout replaces: it is prose, not a listing.
    expect(callout.querySelector('pre')).toBeNull();
    expect(container.querySelector('pre')).toBeNull();
  });

  it('is still a callout when its body is empty', () => {
    // An empty fence carries no child at all. Falling through would dress it in
    // the sunken code chrome a callout exists to replace.
    const { container } = render(<Markdown source={'```callout\n```'} />);
    expect(screen.getByTestId('callout')).toBeInTheDocument();
    expect(container.querySelector('pre')).toBeNull();
  });

  it('stops nesting callouts past a fixed depth, so agent prose cannot recurse without limit', () => {
    // A four-backtick fence keeps a three-backtick fence inside it verbatim, so
    // an agent quoting a transcript that held a callout drives this recursion.
    const source = '````callout\nouter\n\n```callout\ninner\n```\n````';
    render(<Markdown source={source} />);
    const callouts = screen.getAllByTestId('callout');
    expect(callouts).toHaveLength(2);
    expect(callouts[0]).toHaveTextContent('outer');
    expect(callouts[1]).toHaveTextContent('inner');
  });

  it('renders the markdown inside it, so a callout can carry a literal or a link', () => {
    render(<Markdown source={'```callout\nRun `mael env reset` or [read the doc](/docs).\n```'} />);
    const callout = screen.getByTestId('callout');
    expect(within(callout).getByText('mael env reset').tagName).toBe('CODE');
    expect(within(callout).getByRole('link', { name: 'read the doc' })).toHaveAttribute(
      'href',
      '/docs',
    );
  });

  it('leaves a fence in another language as a code block', () => {
    // This override sits in front of every code block an agent writes, so the
    // ordinary case must survive it untouched.
    const { container } = render(<Markdown source={'```js\nconst x = 1;\n```'} />);
    expect(screen.queryByTestId('callout')).toBeNull();
    expect(container.querySelector('pre')).not.toBeNull();
    expect(screen.getByText(/const x = 1;/)).toBeInTheDocument();
  });

  it('leaves a fence with no info string as a code block', () => {
    const { container } = render(<Markdown source={'```\nplain listing\n```'} />);
    expect(screen.queryByTestId('callout')).toBeNull();
    expect(container.querySelector('pre')).not.toBeNull();
    expect(screen.getByText(/plain listing/)).toBeInTheDocument();
  });
});
