import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { faultKey } from './fake/operations';
import { paneItem } from './test/appHelpers';
import { renderApp } from './test/renderApp';

describe('the projects view', () => {
  const row = (id: string) =>
    document.querySelector(
      `[data-testid="project-list"] [data-project-id="${id}"]`,
    ) as HTMLElement | null;
  const listedIds = () =>
    [...document.querySelectorAll('[data-testid="project-list"] [data-project-id]')].map((r) =>
      r.getAttribute('data-project-id'),
    );
  const openAdd = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(await screen.findByRole('button', { name: 'Add project' }));
    return within(screen.getByRole('dialog', { name: 'Add project' }));
  };
  const taskPosts = (server: { requests: { method: string; path: string; body?: unknown }[] }) =>
    server.requests.filter((r) => r.path === '/api/tasks' && r.method === 'POST');
  const setUpPost = (project: string) => ({
    method: 'POST',
    path: '/api/tasks',
    body: {
      project,
      title: `Set up ${project}`,
      branch: 'chore/setup-project',
      command: 'setup-project',
      mode: 'normal',
      launch: true,
    },
  });

  it('is a tab, at /projects, with one row per project of the world', async () => {
    const user = userEvent.setup();
    const { server, router } = await renderApp();
    const topBar = within(screen.getByTestId('top-bar'));
    expect(topBar.getByLabelText('Project')).toBeInTheDocument();
    await user.click(paneItem('Projects'));
    expect(router.state.location.pathname).toBe('/projects');
    // The view lists every project, so the bar offers no Project filter.
    expect(topBar.queryByLabelText('Project')).toBeNull();

    const projects = Object.keys(server.world.projects).sort();
    await waitFor(() => expect(listedIds()).toEqual(projects));
    const open = Object.values(server.world.worktrees).filter(
      (w) => w.project === projects[0] && !w.isClosed,
    ).length;
    expect(row(projects[0]!)).toHaveTextContent(`${open} open`);
  });

  it('opens a project from its row, with its facts in the URL-held dialog', async () => {
    const user = userEvent.setup();
    const { server, router } = await renderApp({ url: '/projects' });
    const id = Object.keys(server.world.projects).sort()[0]!;
    await waitFor(() => expect(row(id)).not.toBeNull());

    await user.click(within(row(id)!).getByText(`${server.world.projects[id]!.stackTip}`));
    const dialog = within(
      await screen.findByRole('dialog', { name: server.world.projects[id]!.name }),
    );
    expect(router.state.location.search).toBe(`?detail=${id}`);
    // The dialog reads the row's facts: the same worktree count as the list.
    expect(dialog.getByText(within(row(id)!).getByText(/ open$/).textContent!)).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(router.state.location.search).toBe('');
  });

  it('sets a project up from its detail dialog', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ url: '/projects' });
    const id = Object.keys(server.world.projects).sort()[0]!;
    await user.click(await screen.findByRole('button', { name: server.world.projects[id]!.name }));
    const dialog = within(await screen.findByRole('dialog'));

    await user.click(dialog.getByRole('button', { name: 'Set up' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(taskPosts(server)).toEqual([setUpPost(id)]);
  });

  it('shows a failed set-up in the detail dialog, and keeps it open', async () => {
    const user = userEvent.setup();
    const { server, router } = await renderApp({ url: '/projects' });
    const id = Object.keys(server.world.projects).sort()[0]!;
    server.refuse(/POST \/api\/tasks$/, {
      status: 500,
      code: 'internal',
      message: 'The notebook is locked',
    });
    await user.click(await screen.findByRole('button', { name: server.world.projects[id]!.name }));
    const dialog = within(await screen.findByRole('dialog'));

    await user.click(dialog.getByRole('button', { name: 'Set up' }));

    expect(await dialog.findByRole('alert')).toHaveTextContent('The notebook is locked');
    expect(router.state.location.search).toBe(`?detail=${id}`);
  });

  it('says so when the detail names no project', async () => {
    await renderApp({ url: '/projects?detail=nope' });
    const dialog = within(await screen.findByRole('dialog', { name: 'nope' }));
    expect(await dialog.findByText('No project nope')).toBeInTheDocument();
  });

  it('adds a project from its git URL, and the new row shows once the clone ends', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ url: '/projects' });
    const dialog = await openAdd(user);

    await user.type(dialog.getByLabelText('Git URL'), 'git@github.com:acme/demo.git');
    await user.click(dialog.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(row('demo')).not.toBeNull());
    expect(screen.queryByRole('dialog', { name: 'Add project' })).toBeNull();
    expect(
      server.requests.filter((r) => r.path === '/api/projects' && r.method === 'POST'),
    ).toEqual([
      { method: 'POST', path: '/api/projects', body: { gitUrl: 'git@github.com:acme/demo.git' } },
    ]);
  });

  it('shows the refusal of a project that exists, and keeps the dialog open', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ url: '/projects' });
    const existing = Object.keys(server.world.projects)[0]!;
    const dialog = await openAdd(user);

    await user.type(dialog.getByLabelText('Git URL'), `git@github.com:acme/${existing}.git`);
    await user.click(dialog.getByRole('button', { name: 'Add' }));

    expect(await dialog.findByRole('alert')).toHaveTextContent(
      `Project ${existing} already exists`,
    );
  });

  it('shows why a clone failed', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ url: '/projects' });
    server.world.operationFaults[faultKey('addProject', 'demo')] = 'Repository not found.';
    const dialog = await openAdd(user);

    await user.type(dialog.getByLabelText('Git URL'), 'git@github.com:acme/demo.git');
    await user.click(dialog.getByRole('button', { name: 'Add' }));

    expect(await dialog.findByRole('alert')).toHaveTextContent(
      'Could not add demo: Repository not found.',
    );
    expect(row('demo')).toBeNull();
  });
});
