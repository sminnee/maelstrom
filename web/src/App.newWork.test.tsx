import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Agent } from './protocol/entities';
import type { FakeServer } from './test/fakeServer';
import { renderApp } from './test/renderApp';

describe('new work', () => {
  /** Open the form from the top bar and return its dialog. */
  async function openNewWork(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: 'New' }));
    return screen.getByRole('dialog', { name: 'New work' });
  }

  /**
   * The agent this run started, not one the seeded world already held.
   * The fake host mints a started agent's id with a `new` prefix, and the
   * seed has free agents of its own — so "the agent with no task" would
   * find one of those and pass whatever the form sent.
   */
  function startedAgent(server: FakeServer): Agent {
    const started = Object.values(server.world.agents).filter((a) => a.id.startsWith('new'));
    expect(started).toHaveLength(1);
    return started[0]!;
  }

  it('is reachable from the top bar in both views', async () => {
    const user = userEvent.setup();
    await renderApp();
    expect(screen.getByRole('button', { name: 'New' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Task list' }));
    expect(screen.getByRole('button', { name: 'New' })).toBeVisible();
  });

  it('dismisses the combo box on Escape without closing the dialog', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.click(within(form).getByRole('radio', { name: 'Free agent' }));
    await user.click(within(form).getByLabelText('Branch'));
    expect(await screen.findByRole('listbox')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    // One press dismisses the offer. A second is what closes the dialog --
    // otherwise the press throws away everything typed so far.
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.getByRole('dialog', { name: 'New work' })).toBeInTheDocument();
  });

  /** The task this run wrote: the fake mints new ids with a `NEW-` segment. */
  function createdTask(server: FakeServer) {
    const written = Object.values(server.world.tasks).filter((t) => t.id.includes('/NEW-'));
    expect(written).toHaveLength(1);
    return written[0]!;
  }

  it('offers the Linear kind only for a project that names a Linear team', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'maelstrom');
    expect(within(form).getByRole('radio', { name: 'Linear' })).toBeInTheDocument();
    // `riverbend` sets no team, so planning a Linear issue is not on offer.
    await user.selectOptions(within(form).getByLabelText('Project'), 'riverbend');
    expect(within(form).queryByRole('radio', { name: 'Linear' })).toBeNull();
  });

  it('falls back to a task when the chosen project drops the Linear kind', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'maelstrom');
    await user.click(within(form).getByRole('radio', { name: 'Linear' }));
    await user.selectOptions(within(form).getByLabelText('Project'), 'riverbend');
    // The kind it was on is gone, so the form must land somewhere legal.
    expect(within(form).getByRole('radio', { name: 'Task' })).toBeChecked();
  });

  it('offers the cycle by issue id, showing each title to choose by', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'maelstrom');
    await user.click(within(form).getByRole('radio', { name: 'Linear' }));

    const issue = await within(form).findByLabelText('Issue');
    await user.click(issue);
    const rows = within(await screen.findByRole('listbox')).getAllByRole('option');
    expect(rows.map((r) => r.textContent)).toEqual([
      'MAEL-70Add a Linear kind to the new panel',
      'MAEL-71Retire the Linear integration',
    ]);
  });

  it('plans the chosen issue, writing the task `mael linear plan` writes', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'maelstrom');
    await user.click(within(form).getByRole('radio', { name: 'Linear' }));
    await user.click(await within(form).findByLabelText('Issue'));
    await user.click(await screen.findByRole('option', { name: /Add a Linear kind/ }));
    await user.click(within(form).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const planned = createdTask(server);
    expect(planned.title).toBe('Plan MAEL-70');
    expect(planned.command).toBe('plan-task');
    expect(planned.parent).toBe('linear.MAEL-70');
    expect(planned.status).toBe('todo');
    expect(server.world.desk[`task:${planned.id}`]).toBeDefined();
  });

  it('starts the planning session when Start is pressed instead', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'maelstrom');
    await user.click(within(form).getByRole('radio', { name: 'Linear' }));
    await user.click(await within(form).findByLabelText('Issue'));
    await user.click(await screen.findByRole('option', { name: /Add a Linear kind/ }));
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const planned = createdTask(server);
    expect(planned.status).toBe('in-progress');
    expect(Object.values(server.world.agents).some((a) => a.taskId === planned.id)).toBe(true);
  });

  it('forgets the chosen issue when the project changes', async () => {
    const user = userEvent.setup();
    await renderApp();
    // Both seeded projects name a Linear team, so the kind survives the switch
    // and a stale issue would be submitted against a project it does not
    // belong to.
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'maelstrom');
    await user.click(within(form).getByRole('radio', { name: 'Linear' }));
    await user.click(await within(form).findByLabelText('Issue'));
    await user.click(await screen.findByRole('option', { name: /Add a Linear kind/ }));
    expect(within(form).getByLabelText('Issue')).toHaveValue('MAEL-70');

    await user.selectOptions(within(form).getByLabelText('Project'), 'northwind');
    // MAEL-70 is not northwind's to plan, so the field must not carry it over.
    expect(within(form).getByLabelText('Issue')).toHaveValue('');
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('holds Save and Start back until an issue is chosen', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'maelstrom');
    await user.click(within(form).getByRole('radio', { name: 'Linear' }));
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(within(form).getByRole('button', { name: 'Start' })).toBeDisabled();
    // The prose field belongs to the other kinds: the brief comes from Linear.
    expect(within(form).queryByLabelText('What needs doing?')).toBeNull();
  });

  it('holds Next back until the draft has something in it', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    expect(within(form).getByRole('button', { name: 'Next' })).toBeDisabled();
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    expect(within(form).getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  it('names the task from the draft, then saves it as todo onto the desk', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'northwind');
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    await user.click(within(form).getByRole('button', { name: 'Next' }));

    // Step 2 arrives with the fields the user never typed, filled in.
    const title = await within(form).findByLabelText('Title');
    expect(title).toHaveValue('The export drops a row');
    // The value comes from the server, so the test pins that a branch was
    // filled in without the user typing one — not the fake's own slug.
    expect((within(form).getByLabelText('Branch') as HTMLInputElement).value).toMatch(/^feat\/.+/);
    // The prose becomes the content verbatim; inference names it, never rewrites it.
    expect(within(form).getByLabelText('Content')).toHaveValue('The export drops a row');

    // Every inferred field stays editable.
    await user.clear(title);
    await user.type(title, 'Fix the export');
    await user.click(within(form).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const created = Object.values(server.world.tasks).find((t) => t.title === 'Fix the export');
    expect(created).toBeDefined();
    expect(created!.status).toBe('todo');
    // Saved work joins the desk, so what was just ordered is on the canvas.
    expect(server.world.desk[`task:${created!.id}`]).toBeDefined();
  });

  it('starts the task it creates when Start is pressed instead', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    await user.click(within(form).getByRole('button', { name: 'Next' }));
    await within(form).findByLabelText('Title');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const created = Object.values(server.world.tasks).find(
      (t) => t.title === 'The export drops a row',
    );
    expect(created!.status).toBe('in-progress');
    expect(Object.values(server.world.agents).some((a) => a.taskId === created!.id)).toBe(true);
  });

  it('starts a free agent on a branch, writing no task at all', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const before = Object.keys(server.world.tasks).length;
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'northwind');
    await user.click(within(form).getByRole('radio', { name: 'Free agent' }));
    await user.type(within(form).getByLabelText('Branch'), 'feat/orders');
    await user.type(within(form).getByLabelText('What needs doing?'), 'Read the logs');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    // No task was written: a free agent is work with no notebook entry.
    expect(Object.keys(server.world.tasks)).toHaveLength(before);
    const free = startedAgent(server);
    expect(free.taskId).toBe('');
    expect(server.world.desk[`agent:${free.id}`]).toBeDefined();
    // Unchosen, a free agent runs the same defaults a new task does.
    expect(free.permissionMode).toBe('plan');
    expect(free.model).toBe('opus');
  });

  it('starts a free agent under the mode and model the form chose', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'northwind');
    await user.click(within(form).getByRole('radio', { name: 'Free agent' }));
    await user.type(within(form).getByLabelText('Branch'), 'feat/orders');
    await user.type(within(form).getByLabelText('What needs doing?'), 'Read the logs');
    await user.selectOptions(within(form).getByLabelText('Mode'), 'auto');
    await user.selectOptions(within(form).getByLabelText('Model'), 'fable');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const free = startedAgent(server);
    expect(free.permissionMode).toBe('auto');
    expect(free.model).toBe('fable');
  });

  it('never offers to write the task twice when only its launch failed', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    await user.click(within(form).getByRole('button', { name: 'Next' }));
    await within(form).findByLabelText('Title');
    // The task is written; the launch that follows it is refused.
    server.refuse(/api\/tasks$/, {
      status: 409,
      code: 'agent_exited',
      message: 'Agent has exited',
      taskId: 'northwind/NEW-1',
    });
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    // The form says the task survived, and stops offering to write it again.
    expect(await within(form).findByTestId('new-work-error')).toHaveTextContent('northwind/NEW-1');
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
    const creates = server.requests.filter((r) => r.method === 'POST' && r.path === '/api/tasks');
    expect(creates).toHaveLength(1);
  });

  it('shows a refused start rather than closing on it', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.refuse(/api\/agents$/, { status: 400, code: 'invalid', message: 'No such branch' });
    const form = await openNewWork(user);
    await user.click(within(form).getByRole('radio', { name: 'Free agent' }));
    await user.type(within(form).getByLabelText('Branch'), 'feat/nope');
    await user.type(within(form).getByLabelText('What needs doing?'), 'Read the logs');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    expect(await within(form).findByTestId('new-work-error')).toHaveTextContent('No such branch');
    // The form stays, holding what was typed.
    expect(screen.getByRole('dialog', { name: 'New work' })).toBeVisible();
  });

  it('starts a free agent with an attached image in its prompt', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.click(within(form).getByRole('radio', { name: 'Free agent' }));
    await user.type(within(form).getByLabelText('Branch'), 'feat/logs');

    const png = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', {
      type: 'image/png',
    });
    await user.upload(within(form).getByLabelText('Attach image', { selector: 'input' }), png);
    const draft = within(form).getByLabelText('What needs doing?');
    await waitFor(() => expect((draft as HTMLTextAreaElement).value).toContain('![shot.png]('));
    await user.type(draft, 'why does this look wrong?');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    const started = await waitFor(() => {
      const found = server.requests.find((r) => r.method === 'POST' && r.path === '/api/agents');
      expect(found).toBeDefined();
      return found!;
    });
    const body = started.body as { prompt: string };
    // A free agent has no say to carry an image block, so the prompt carries
    // the token the agent reads from disk.
    expect(body.prompt).toContain('{{MAEL_TASK_DIR}}');
    expect(body.prompt).toContain('why does this look wrong?');
  });

  it('attaches an image to a task, which stores the portable token', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'Fix the header');
    await user.click(within(form).getByRole('button', { name: 'Next' }));

    const png = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', {
      type: 'image/png',
    });
    await user.upload(within(form).getByLabelText('Attach image', { selector: 'input' }), png);
    const content = within(form).getByLabelText('Content');
    await waitFor(() => expect((content as HTMLTextAreaElement).value).toContain('![shot.png]('));
    await user.click(within(form).getByRole('button', { name: 'Save' }));

    const created = await waitFor(() => {
      const found = server.requests.find((r) => r.method === 'POST' && r.path === '/api/tasks');
      expect(found).toBeDefined();
      return found!;
    });
    // The stored content holds the token, never the fetch URL: it has to
    // survive a re-clone on another machine.
    expect((created.body as { content: string }).content).toContain('{{MAEL_TASK_DIR}}');
  });
});
