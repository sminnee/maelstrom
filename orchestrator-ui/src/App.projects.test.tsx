import { describe, expect, it } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
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

  it('adds a project from its git URL, then sets it up before the dialog closes', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ url: '/projects' });
    const dialog = await openAdd(user);

    await user.type(dialog.getByLabelText('Git URL'), 'git@github.com:acme/demo.git');
    await user.click(dialog.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(row('demo')).not.toBeNull());
    // The Project dialog of the new project takes the Add dialog's place.
    await screen.findByRole('dialog', { name: 'demo' });
    expect(screen.queryByRole('dialog', { name: 'Add project' })).toBeNull();
    expect(
      server.requests.filter((r) => r.path === '/api/projects' && r.method === 'POST'),
    ).toEqual([
      { method: 'POST', path: '/api/projects', body: { gitUrl: 'git@github.com:acme/demo.git' } },
    ]);
    expect(taskPosts(server)).toEqual([setUpPost('demo')]);
  });

  it('keeps the dialog open on a failed set-up, and retries the set-up alone', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ url: '/projects' });
    server.refuse(/POST \/api\/tasks$/, {
      status: 500,
      code: 'internal',
      message: 'The notebook is locked',
    });
    const dialog = await openAdd(user);

    await user.type(dialog.getByLabelText('Git URL'), 'git@github.com:acme/demo.git');
    await user.click(dialog.getByRole('button', { name: 'Add' }));

    expect(await dialog.findByRole('alert')).toHaveTextContent('The notebook is locked');
    expect(row('demo')).not.toBeNull();
    server.allow();
    await user.click(dialog.getByRole('button', { name: 'Retry set-up' }));

    await screen.findByRole('dialog', { name: 'demo' });
    expect(screen.queryByRole('dialog', { name: 'Add project' })).toBeNull();
    expect(
      server.requests.filter((r) => r.path === '/api/projects' && r.method === 'POST'),
    ).toHaveLength(1);
    expect(taskPosts(server).at(-1)).toEqual(setUpPost('demo'));
  });

  it('adds a project and sets nothing up with the box off', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ url: '/projects' });
    const dialog = await openAdd(user);

    await user.click(dialog.getByLabelText('Set up after adding'));
    await user.type(dialog.getByLabelText('Git URL'), 'git@github.com:acme/demo.git');
    await user.click(dialog.getByRole('button', { name: 'Add' }));

    await screen.findByRole('dialog', { name: 'demo' });
    expect(row('demo')).not.toBeNull();
    expect(taskPosts(server)).toEqual([]);
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

  it('offers Create task on a project with work, and it opens New work on the project', async () => {
    const user = userEvent.setup();
    await renderApp({ url: '/projects?detail=northwind' });
    const dialog = within(await screen.findByRole('dialog', { name: 'northwind' }));

    await user.click(await dialog.findByRole('button', { name: 'Create task' }));

    const form = within(await screen.findByRole('dialog', { name: 'New work' }));
    expect(form.getByRole('radio', { name: 'northwind' })).toBeChecked();
  });
});

describe('the Welcome dialog', () => {
  const welcome = () => screen.queryByRole('dialog', { name: 'Welcome to maelstrom' });

  it('opens itself on a fresh install, and stays shut once closed', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'first-run' });

    const dialog = within(await screen.findByRole('dialog', { name: 'Welcome to maelstrom' }));
    expect(await dialog.findByText('~/Projects')).toBeInTheDocument();
    expect(dialog.getByText('mael, plan-task, planning, watch-pr')).toBeInTheDocument();
    expect(dialog.getByText('~/.maelstrom/plugins/maelstrom/skills')).toBeInTheDocument();
    expect(dialog.getByTestId('welcome-practices')).toHaveTextContent('minnstack');
    expect(dialog.getByText('~/.maelstrom')).toBeInTheDocument();
    expect(dialog.getByTestId('welcome-version')).toHaveTextContent('v0.9.0 (3f2a9c1e7b4d)');
    expect(dialog.getByTestId('welcome-host')).toHaveTextContent('reachable');
    expect(dialog.getByTestId('welcome-machine')).toHaveTextContent('No samples yet');

    await user.click(dialog.getAllByRole('button', { name: 'Close' }).at(-1)!);
    await waitFor(() => expect(welcome()).toBeNull());
    await user.click(paneItem('Projects'));
    expect(welcome()).toBeNull();
  });

  it('does not open itself while the world has a project, and the logo opens it', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp();
    expect(welcome()).toBeNull();

    await user.click(screen.getByRole('button', { name: 'About maelstrom' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Welcome to maelstrom' }));
    expect(router.state.location.search).toBe('?welcome=1');
    expect(dialog.getByRole('button', { name: 'Add project' })).toBeInTheDocument();

    // The seed's last sample: job 41 uses 212.3% of one core of 8, and 1.2G.
    const now = await dialog.findByTestId('machine-now');
    expect(now).toHaveTextContent('CPU 47% · mem 12G of 32G · 62 °C');
    expect(now).toHaveTextContent('maelstrom-alpha cpu 27% · mem 1.2G');
    expect(now).toHaveTextContent('other cpu 20% · mem 11G');
    expect(dialog.getByRole('img', { name: 'CPU, last 15 minutes' })).toBeInTheDocument();
  });

  it('adds the first project, then opens its Project dialog on its paths', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp({ scenario: 'first-run' });
    const dialog = within(await screen.findByRole('dialog', { name: 'Welcome to maelstrom' }));

    await user.click(dialog.getByRole('button', { name: 'Add your first project' }));
    const add = within(await screen.findByRole('dialog', { name: 'Add project' }));
    expect(welcome()).toBeNull();
    await user.type(add.getByLabelText('Git URL'), 'git@github.com:acme/demo.git');
    await user.click(add.getByRole('button', { name: 'Add' }));

    const project = within(await screen.findByRole('dialog', { name: 'demo' }));
    expect(router.state.location.search).toBe('?detail=demo');
    expect(project.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
    expect(await project.findByTestId('project-main-path')).toHaveTextContent(
      '~/Projects/demo/_main',
    );
    expect(project.getByTestId('project-worktree-paths')).toHaveTextContent(
      '~/Projects/demo/demo-alpha..zulu',
    );
    // The Welcome dialog does not come back over the new project.
    expect(welcome()).toBeNull();

    // The hand-over replaced Add project, so Back does not reopen it.
    await act(() => router.navigate(-1));
    expect(router.state.location.search).toBe('');
    expect(screen.queryByRole('dialog', { name: 'Add project' })).toBeNull();
  });

  it("switches the Project dialog's tab in the URL, and Back closes the dialog", async () => {
    const user = userEvent.setup();
    const { router } = await renderApp({ url: '/projects' });
    await user.click(await screen.findByRole('button', { name: 'northwind' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'northwind' }));

    await user.click(dialog.getByRole('tab', { name: 'Worktrees' }));
    expect(router.state.location.search).toBe('?detail=northwind&dtab=worktrees');
    expect(dialog.getByTestId('worktree-table')).toBeInTheDocument();

    await act(() => router.navigate(-1));
    expect(router.state.location.search).toBe('');
  });

  async function openWelcome(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: 'About maelstrom' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Welcome to maelstrom' }));
    return within(await dialog.findByTestId('welcome-update'));
  }

  it('confirms an update with the live agent count, then follows the job through the restart', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.world.system.updateChannel = 'main';
    // The last job's record: the new one must replace it before the poll may stop.
    server.world.system.update = {
      state: 'succeeded',
      pid: 1,
      startedAt: 't0',
      finishedAt: 't1',
      fromTag: 'v0.8.0',
      toTag: 'v0.9.0',
      exitCode: 0,
      log: '/Users/dev/.maelstrom/logs/update.log',
    };
    // One exited agent, so the count is not every agent.
    await act(async () => {
      server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
        w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'exited', exitCode: 0 };
      });
    });
    const row = await openWelcome(user);
    expect(row.getByText('from the main channel')).toBeInTheDocument();

    await user.click(row.getByRole('button', { name: 'Update maelstrom' }));
    expect(
      await row.findByText(/^The update restarts .* 5 live agents stop\./),
    ).toBeInTheDocument();
    await user.click(row.getByRole('button', { name: 'Update' }));

    // The server answered, but the job has not written its record yet.
    expect(await row.findByText('Updating…')).toBeInTheDocument();
    // The orchestrator restarts: a poll fails, and the dialog keeps its body.
    const reads = () => server.requests.filter((r) => r.path === '/api/system').length;
    const before = reads();
    server.refuse(/GET \/api\/system/, { status: 502, code: 'unavailable' });
    await waitFor(() => expect(reads()).toBeGreaterThan(before), { timeout: 5000 });
    expect(row.getByText('Updating…')).toBeInTheDocument();
    expect(screen.queryByText(/Could not read the install/)).toBeNull();

    server.allow();
    server.world.system.update = {
      ...server.world.system.update,
      pid: 4242,
      startedAt: 't2',
      fromTag: 'v0.9.0',
      toTag: 'v1.0.0',
    };
    expect(await row.findByText('Updated to v1.0.0', {}, { timeout: 5000 })).toBeInTheDocument();
    // The page still runs the old bundle.
    expect(row.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  });

  it('shows why the server refused to start the job', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.refuse(/POST \/api\/self\/update/, {
      status: 409,
      code: 'cannot_update',
      message: 'An update is running.',
    });
    const row = await openWelcome(user);

    await user.click(row.getByRole('button', { name: 'Update maelstrom' }));
    await user.click(row.getByRole('button', { name: 'Update' }));

    expect(await row.findByText('An update is running.')).toBeInTheDocument();
    expect(row.queryByText('Updating…')).toBeNull();
  });

  it('cancels the confirm without starting a job', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const row = await openWelcome(user);

    await user.click(row.getByRole('button', { name: 'Update maelstrom' }));
    await user.click(row.getByRole('button', { name: 'Cancel' }));

    expect(row.getByRole('button', { name: 'Update maelstrom' })).toBeInTheDocument();
    expect(server.world.system.update).toBeNull();
  });

  it.each([
    [{ state: 'succeeded', fromTag: 'v1', toTag: 'v1', exitCode: 0 }, 'Already up to date (v1)'],
    [{ state: 'succeeded', fromTag: 'v1', toTag: 'v2', exitCode: 0 }, 'Updated to v2'],
    [
      { state: 'failed', exitCode: 2 },
      'The last update failed (exit 2). The log is ~/.maelstrom/logs/update.log.',
    ],
    [
      { state: 'interrupted' },
      'The last update stopped before it ended. The log is ~/.maelstrom/logs/update.log.',
    ],
  ] as const)('shows the last job %j and offers the update again', async (fields, words) => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.world.system.update = {
      pid: 1,
      startedAt: 't0',
      finishedAt: 't1',
      fromTag: 'v1',
      toTag: 'v1',
      exitCode: null,
      log: '/Users/dev/.maelstrom/logs/update.log',
      ...fields,
    };
    const row = await openWelcome(user);

    expect(screen.getByTestId('welcome-update')).toHaveTextContent(words);
    expect(row.getByRole('button', { name: 'Update maelstrom' })).toBeEnabled();
    // A job this page did not start: the page already runs its bundle.
    expect(row.queryByRole('button', { name: 'Reload' })).toBeNull();
  });

  it('disables the update on a development build', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.world.system.canUpdate = false;
    const row = await openWelcome(user);

    expect(row.getByRole('button', { name: 'Update maelstrom' })).toBeDisabled();
    expect(row.getByText('development build')).toBeInTheDocument();
  });

  it('says when the version is a development build and the host does not answer', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    // Nothing has read the install yet: only the Welcome dialog asks for it.
    server.world.system.version = null;
    await act(async () => {
      server.change({ kind: 'host', ids: ['agent-host'] }, (world) => {
        world.host = { ...world.host!, reachable: false };
      });
    });

    await user.click(screen.getByRole('button', { name: 'About maelstrom' }));

    const dialog = within(await screen.findByRole('dialog', { name: 'Welcome to maelstrom' }));
    expect(await dialog.findByText('development build')).toBeInTheDocument();
    await waitFor(() =>
      expect(dialog.getByTestId('welcome-host')).toHaveTextContent('not reachable'),
    );
  });
});
