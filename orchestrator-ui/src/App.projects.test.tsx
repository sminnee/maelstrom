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
