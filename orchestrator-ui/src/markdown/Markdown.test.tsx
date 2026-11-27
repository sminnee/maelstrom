import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { createMemoryRouter } from 'react-router';
import { ApiProvider } from '../api/ApiProvider';
import { createFakeServer } from '../fake/fakeServer';
import { makeAgent, makeDocument, makeTask, makeWorktree, worldWith } from '../fake/fixtures';
import { comm } from '../fake/seedWorld';
import { parseLocation, type Loc } from '../nav/location';
import { NavRouter } from '../nav/NavRouter';
import { routesFor } from '../nav/routes';
import { setViewportWidth } from '../test/setup';
import { HEADING_ID_PREFIX, Markdown, attentionSegments } from './Markdown';

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

  it('switches rank on each inline tag and keeps the text after it', () => {
    // Codex writes this shape; the tag sets the rank and the text stays.
    const source = 'Answer.\n\n<user-attention low>Detail.\n\n<user-attention high>Done.';
    const { container } = render(<Markdown source={source} />);
    const quiet = screen.getByTestId('quiet');
    expect(quiet).toHaveTextContent('Detail.');
    expect(quiet).not.toHaveTextContent('Answer.');
    expect(quiet).not.toHaveTextContent('Done.');
    expect(screen.getByText('Answer.')).toBeInTheDocument();
    expect(screen.getByText('Done.')).toBeInTheDocument();
    expect(container).not.toHaveTextContent('user-attention');
  });

  it('reads a fence that opens on the tag line, so a tag inside it stays literal', () => {
    const source = '<user-attention low>```\n<user-attention high>\n```\nAfter.';
    expect(attentionSegments(source)).toEqual([
      { attention: 'low', source: '```\n<user-attention high>\n```\nAfter.' },
    ]);
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

describe('a video in a document', () => {
  it.each(['webm', 'mp4', 'mov'])('draws a .%s as a player the reader controls', (ext) => {
    const src = `/api/attachments/northwind/NORT-7/flow.${ext}`;
    const { container } = render(<Markdown source={`![The login flow](${src})`} />);
    const video = container.querySelector('video');
    expect(video).toHaveAttribute('src', src);
    expect(video).toHaveAttribute('controls');
    expect(video).toHaveAttribute('aria-label', 'The login flow');
    // A video is not a thumbnail: nothing opens a lightbox.
    expect(screen.queryByRole('button', { name: /The login flow/ })).not.toBeInTheDocument();
  });

  it('reads the extension off the path, not off a query string', () => {
    const { container } = render(<Markdown source="![Flow](/api/files/flow.webm?v=2)" />);
    expect(container.querySelector('video')).not.toBeNull();
  });

  it('leaves an image an image', () => {
    const { container } = render(<Markdown source={SHOT} />);
    expect(container.querySelector('video')).toBeNull();
    expect(screen.getByAltText('The failing dialog')).toBeInTheDocument();
  });
});

describe('a table in a message', () => {
  const TABLE = '| Feature | Action |\n| --- | --- |\n| Files | File existing |';

  it('scrolls in a region a keyboard can reach, and stays a table', async () => {
    const user = userEvent.setup();
    render(<Markdown source={TABLE} />);

    await user.tab();

    const region = screen.getByRole('region', { name: 'Table' });
    expect(region).toHaveFocus();
    const table = within(region).getByRole('table');
    expect(within(table).getByRole('columnheader', { name: 'Action' })).toBeInTheDocument();
  });
});

/** Shared with the server's fragment test, so a link's fragment finds its heading. */
const SLUG_CASES: { name: string; markdown: string; slugs: string[] }[] = JSON.parse(
  readFileSync(resolve(__dirname, '../../../lib/domain/fixtures/heading-slugs.json'), 'utf8'),
).cases;

describe('heading ids', () => {
  it.each(SLUG_CASES.map((c) => [c.name, c] as const))(
    'name each heading as the server names its fragment: %s',
    (_, { markdown, slugs }) => {
      const { container } = render(<Markdown source={markdown} headingIds />);
      const ids = [...container.querySelectorAll('h1, h2, h3, h4, h5, h6')].map((h) => h.id);
      expect(ids).toEqual(slugs.map((slug) => HEADING_ID_PREFIX + slug));
    },
  );

  it('are off by default, so a page of messages repeats no id', () => {
    const { container } = render(<Markdown source="# Set up" />);
    expect(container.querySelector('h1')).not.toHaveAttribute('id');
  });
});

describe('a mael: link in a message', () => {
  const PR_URL = 'https://github.com/o/r/pull/118';
  const TASK_ID = 'northwind/NORT-7';

  function renderCards(source: string, viewport: 'wide' | 'narrow' = 'wide') {
    setViewportWidth(viewport === 'wide' ? 1920 : 390);
    const world = worldWith({
      tasks: [makeTask({ id: TASK_ID, title: 'Add order export' })],
      agents: [makeAgent({ id: 'ag1', taskId: TASK_ID, description: 'Order export' })],
      worktrees: [makeWorktree({ prNumber: 118, prUrl: PR_URL, prState: 'ready' })],
      documents: [makeDocument({ id: 'doc-1', title: 'Plan', status: 'awaiting-review' })],
    });
    world.comms.c1 = comm({ id: 'c1', title: 'Release notes', codename: 'brisk-otter' });
    const server = createFakeServer({ world });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter(routesFor(<Markdown source={source} />), {
      initialEntries: ['/desk/graph'],
    });
    render(
      <ApiProvider api={server.api} queryClient={queryClient}>
        <NavRouter router={router} />
      </ApiProvider>,
    );
    const loc = () => parseLocation(router.state.location.pathname, router.state.location.search);
    return { loc };
  }

  const card = () => screen.findByRole('link', { name: /Open|Review/ });

  it.each<[string, string, (loc: Loc) => unknown, unknown]>([
    ['a task', 'mael:task/northwind/NORT-7', (l) => l.card, { kind: 'task', id: TASK_ID }],
    ['a session', 'mael:session/ag1', (l) => l.panel?.key, 'session:ag1'],
    ['a document', 'mael:doc/doc-1', (l) => l.panel?.key, 'document:doc-1'],
    ['a comm', 'mael:comm/brisk-otter', (l) => l.commDetail, 'c1'],
    [
      'a worktree',
      'mael:worktree/northwind/alpha',
      (l) => l.card,
      { kind: 'worktree', id: 'northwind-alpha' },
    ],
  ])('opens %s on a tap', async (_case, uri, read, expected) => {
    const user = userEvent.setup();
    const { loc } = renderCards(`- [The item](${uri})`);
    await user.click(await card());
    expect(read(loc()!)).toEqual(expected);
  });

  it('opens a worktree’s changes on a narrow screen, which has no worktree card', async () => {
    const user = userEvent.setup();
    const { loc } = renderCards('[WT](mael:worktree/northwind/alpha)', 'narrow');
    await user.click(await card());
    expect(loc()!.panel?.key).toBe('changes:northwind-alpha');
  });

  it('opens a PR on GitHub', async () => {
    renderCards('[PR](mael:pr/northwind/118)');
    const link = await screen.findByRole('link', { name: /Open on GitHub/ });
    expect(link).toHaveAttribute('href', PR_URL);
  });

  it('says a target the world does not hold cannot be found, with no action', async () => {
    renderCards('[The lost task](mael:task/northwind/NORT-404)');
    expect(await screen.findByText('The lost task cannot be found')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('keeps a footnote link’s own attributes', () => {
    const { container } = render(<Markdown source={'Claim.[^1]\n\n[^1]: The source.'} />);
    const back = container.querySelector('a[data-footnote-backref]');
    expect(back).toHaveAttribute('href', expect.stringMatching(/^#/));
    expect(back).toHaveAttribute('aria-label');
  });

  it('leaves an ordinary link an ordinary link', async () => {
    renderCards('[Docs](https://example.com/docs)');
    const link = await screen.findByRole('link', { name: 'Docs' });
    expect(link).toHaveAttribute('href', 'https://example.com/docs');
    expect(screen.queryByTestId('link-card')).not.toBeInTheDocument();
  });
});
