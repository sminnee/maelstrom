import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Agent } from './protocol/entities';
import type { FakeServer } from './test/fakeServer';
import { renderApp } from './test/renderApp';
import { retainedKey } from './ui/retained';

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
    await user.click(screen.getByRole('button', { name: 'Tasks' }));
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
    await user.click(within(form).getByRole('radio', { name: 'maelstrom' }));
    expect(within(form).getByRole('radio', { name: 'Linear' })).toBeInTheDocument();
    // `riverbend` sets no team, so planning a Linear issue is not on offer. It
    // has nothing on the canvas either, so it is reached behind Other.
    await user.click(within(form).getByRole('radio', { name: 'Other' }));
    await user.selectOptions(within(form).getByLabelText('Other project'), 'riverbend');
    expect(within(form).queryByRole('radio', { name: 'Linear' })).toBeNull();
  });

  it('falls back to a task when the chosen project drops the Linear kind', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.click(within(form).getByRole('radio', { name: 'maelstrom' }));
    await user.click(within(form).getByRole('radio', { name: 'Linear' }));
    await user.click(within(form).getByRole('radio', { name: 'Other' }));
    await user.selectOptions(within(form).getByLabelText('Other project'), 'riverbend');
    // The kind it was on is gone, so the form must land somewhere legal.
    expect(within(form).getByRole('radio', { name: 'Task' })).toBeChecked();
  });

  it('offers the cycle by issue id, showing each title to choose by', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.click(within(form).getByRole('radio', { name: 'maelstrom' }));
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
    await user.click(within(form).getByRole('radio', { name: 'maelstrom' }));
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
    await user.click(within(form).getByRole('radio', { name: 'maelstrom' }));
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
    await user.click(within(form).getByRole('radio', { name: 'maelstrom' }));
    await user.click(within(form).getByRole('radio', { name: 'Linear' }));
    await user.click(await within(form).findByLabelText('Issue'));
    await user.click(await screen.findByRole('option', { name: /Add a Linear kind/ }));
    expect(within(form).getByLabelText('Issue')).toHaveValue('MAEL-70');

    await user.click(within(form).getByRole('radio', { name: 'northwind' }));
    // MAEL-70 is not northwind's to plan, so the field must not carry it over.
    expect(within(form).getByLabelText('Issue')).toHaveValue('');
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('holds Save and Start back until an issue is chosen', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.click(within(form).getByRole('radio', { name: 'maelstrom' }));
    await user.click(within(form).getByRole('radio', { name: 'Linear' }));
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(within(form).getByRole('button', { name: 'Start' })).toBeDisabled();
    // The prose field belongs to the other kinds: the brief comes from Linear.
    expect(within(form).queryByLabelText('What needs doing?')).toBeNull();
  });

  it('holds Save back until the draft has something in it', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    // One step, so the prose gates the submit itself rather than a Next.
    expect(within(form).queryByRole('button', { name: 'Next' })).toBeNull();
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    expect(within(form).getByRole('button', { name: 'Save' })).toBeEnabled();
  });

  it('reaches Save on the prose alone, naming the task from it', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.click(within(form).getByRole('radio', { name: 'northwind' }));
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    // No Suggest pressed and nothing else typed: the prose is the whole input.
    await user.click(within(form).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const created = createdTask(server);
    // A task needs a title and a branch, and neither was typed, so the form
    // answers for both rather than refusing the save.
    expect(created.title).toBe('The export drops a row');
    expect(created.branch).toBe('feat/export-drops-row');
    // The prose becomes the content verbatim; naming never rewrites it.
    expect(created.content).toBe('The export drops a row');
    expect(created.status).toBe('todo');
    // Saved work joins the desk, so what was just ordered is on the canvas.
    expect(server.world.desk[`task:${created.id}`]).toBeDefined();
  });

  it('fills the branch from the draft when Suggest is pressed, without the user typing one', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    // Empty until asked: inference no longer gates the submit, so it runs only
    // when the user wants a better name than the draft's own.
    expect(within(form).getByLabelText('Branch')).toHaveValue('');
    await user.click(within(form).getByRole('button', { name: 'Suggest' }));

    // The value comes from the server, so this pins that a branch was filled in
    // without the user typing one — not the fake's own slug.
    await waitFor(() =>
      expect((within(form).getByLabelText('Branch') as HTMLInputElement).value).toMatch(
        /^feat\/.+/,
      ),
    );
    expect(within(form).getByLabelText('Title')).toHaveValue('The export drops a row');
  });

  it('shows the wait on the Suggest button itself, not beside the footer buttons', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    // Inference shells out to a model and takes tens of seconds, so the button
    // that started it is where the wait belongs.
    server.hold();
    await user.click(within(form).getByRole('button', { name: 'Suggest' }));

    const suggest = await waitFor(() => {
      const button = within(form).getByRole('button', { name: /Suggest/ });
      expect(button).toHaveAttribute('aria-busy', 'true');
      return button;
    });
    expect(within(suggest).getByTestId('spinner')).toBeInTheDocument();
    // One wait on screen, on the control that owns it.
    expect(within(form).getAllByTestId('spinner')).toHaveLength(1);

    server.release();
    await waitFor(() => expect(suggest).not.toHaveAttribute('aria-busy'));
  });

  it('lets every field the form named stay editable', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    const title = within(form).getByLabelText('Title');
    await user.clear(title);
    await user.type(title, 'Fix the export');
    await user.click(within(form).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    expect(createdTask(server).title).toBe('Fix the export');
  });

  it('starts the task it creates when Start is pressed instead', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
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
    await user.click(within(form).getByRole('radio', { name: 'northwind' }));
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
    expect(free.model).toBe('claude:opus');
  });

  it('starts a free agent under the mode and model the form chose', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.click(within(form).getByRole('radio', { name: 'northwind' }));
    await user.click(within(form).getByRole('radio', { name: 'Free agent' }));
    await user.type(within(form).getByLabelText('Branch'), 'feat/orders');
    await user.type(within(form).getByLabelText('What needs doing?'), 'Read the logs');
    await user.selectOptions(within(form).getByLabelText('Mode'), 'auto');
    await user.selectOptions(within(form).getByLabelText('Model'), 'claude:fable');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const free = startedAgent(server);
    expect(free.permissionMode).toBe('auto');
    expect(free.model).toBe('claude:fable');
  });

  it('never offers to write the task twice when only its launch failed', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
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

  it('shows a refused start rather than closing on it, and still holds it once closed', async () => {
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

    // A refusal is not a submit, so closing on one loses nothing either.
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const reopened = await openNewWork(user);
    expect(within(reopened).getByLabelText('What needs doing?')).toHaveValue('Read the logs');
  });

  it('holds the prose across a close, so Escape is not a discard', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.type(
      within(form).getByLabelText('What needs doing?'),
      'The CSV export drops the last row',
    );
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());

    const reopened = await openNewWork(user);
    expect(within(reopened).getByLabelText('What needs doing?')).toHaveValue(
      'The CSV export drops the last row',
    );
  });

  it('holds an attachment with its bucket, so removing it still empties the text', async () => {
    // The test that fails if `attached` or `bucket` is dropped from what is
    // held. `withoutRef` matches on a ref that embeds the bucket, so a
    // re-minted one makes Remove a silent no-op: the thumbnail goes and the ref
    // stays, leaving the agent a link to an image it was never sent.
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    const png = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', {
      type: 'image/png',
    });
    await user.upload(within(form).getByLabelText('Attach image', { selector: 'input' }), png);
    const draft = within(form).getByLabelText('What needs doing?') as HTMLTextAreaElement;
    await waitFor(() => expect(draft.value).toContain('![shot.png]('));

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const reopened = await openNewWork(user);

    // The strip still offers it, so the attachment came back and not just its text.
    const remove = within(reopened).getByRole('button', { name: 'Remove shot.png' });
    await user.click(remove);
    expect(within(reopened).getByLabelText('What needs doing?')).toHaveValue('');
  });

  it('holds nothing once the work is saved', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    await user.click(within(form).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());

    // Closed is not submitted, and submitted is not held: the prose became a
    // task, so reopening starts clean.
    const reopened = await openNewWork(user);
    expect(within(reopened).getByLabelText('What needs doing?')).toHaveValue('');
  });

  it('empties the field on Clear, and holds nothing after it', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'Never mind');
    await user.click(within(form).getByRole('button', { name: 'Clear' }));
    expect(within(form).getByLabelText('What needs doing?')).toHaveValue('');

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const reopened = await openNewWork(user);
    expect(within(reopened).getByLabelText('What needs doing?')).toHaveValue('');
  });

  it('drops a held project the world no longer has', async () => {
    const user = userEvent.setup();
    await renderApp();
    // A project chosen under a world that offered it, held past its removal.
    // Through the key table, so a version bump moves this with the code rather
    // than leaving the test writing a key nothing reads and still passing.
    localStorage.setItem(
      retainedKey.newWork(),
      JSON.stringify({ project: 'gone-away', draft: 'Fix the header' }),
    );
    const form = await openNewWork(user);
    // The prose is held, but the dead project must not be: `chosen` falls back
    // to the *first* project, so a stale name would silently write the work
    // against whichever one that is.
    expect(within(form).getByLabelText('What needs doing?')).toHaveValue('Fix the header');
    expect(within(form).queryByRole('radio', { name: 'gone-away' })).toBeNull();
    // Something legal is chosen instead, rather than nothing at all.
    const chosen = within(form)
      .getAllByRole('radio')
      .filter((r) => (r as HTMLInputElement).checked)
      .map((r) => (r as HTMLInputElement).value);
    expect(chosen).toContain('maelstrom');
  });

  describe('the project radios', () => {
    it('offers a radio per project in the canvas view, and several when several are drawn', async () => {
      const user = userEvent.setup();
      await renderApp();
      const form = await openNewWork(user);
      // The seed draws work in two of its three projects, so both are offered
      // and the one with nothing drawn is not.
      expect(within(form).getByRole('radio', { name: 'maelstrom' })).toBeInTheDocument();
      expect(within(form).getByRole('radio', { name: 'northwind' })).toBeInTheDocument();
      expect(within(form).queryByRole('radio', { name: 'riverbend' })).toBeNull();
    });

    it('selects the one project in view, so the common case is no click at all', async () => {
      const user = userEvent.setup();
      await renderApp();
      // Through the filter bar rather than a reseed: the radios follow the
      // canvas, so narrowing it is what expresses "one project in view".
      await user.selectOptions(screen.getByLabelText('Project'), 'maelstrom');
      const form = await openNewWork(user);

      expect(within(form).getByRole('radio', { name: 'maelstrom' })).toBeChecked();
      expect(within(form).queryByRole('radio', { name: 'northwind' })).toBeNull();
    });

    it('reveals the remaining projects behind Other', async () => {
      const user = userEvent.setup();
      await renderApp();
      await user.selectOptions(screen.getByLabelText('Project'), 'maelstrom');
      const form = await openNewWork(user);
      // Not on offer until asked: a project outside the view is the exception.
      expect(within(form).queryByLabelText('Other project')).toBeNull();

      await user.click(within(form).getByRole('radio', { name: 'Other' }));
      const others = within(form).getByLabelText('Other project');
      // Every project the radios do not already name.
      expect([...(others as HTMLSelectElement).options].map((o) => o.value)).toEqual([
        'northwind',
        'riverbend',
      ]);
    });

    it('writes the work against the project the chosen radio names', async () => {
      const user = userEvent.setup();
      const { server } = await renderApp();
      const form = await openNewWork(user);
      await user.click(within(form).getByRole('radio', { name: 'northwind' }));
      await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
      await user.click(within(form).getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
      expect(createdTask(server).project).toBe('northwind');
    });

    it('writes against a project chosen behind Other', async () => {
      const user = userEvent.setup();
      const { server } = await renderApp();
      await user.selectOptions(screen.getByLabelText('Project'), 'maelstrom');
      const form = await openNewWork(user);
      await user.click(within(form).getByRole('radio', { name: 'Other' }));
      await user.selectOptions(within(form).getByLabelText('Other project'), 'riverbend');
      await user.type(within(form).getByLabelText('What needs doing?'), 'Fix the header');
      await user.click(within(form).getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
      expect(createdTask(server).project).toBe('riverbend');
    });

    it('offers every project when the canvas draws none', async () => {
      const user = userEvent.setup();
      await renderApp();
      // `riverbend` has no work on the desk, so filtering to it draws nothing.
      // With no view to read a project off, every project is offered rather
      // than an empty fieldset.
      await user.selectOptions(screen.getByLabelText('Project'), 'riverbend');
      const form = await openNewWork(user);
      for (const name of ['maelstrom', 'northwind', 'riverbend']) {
        expect(within(form).getByRole('radio', { name })).toBeInTheDocument();
      }
    });
  });

  describe('the planning level', () => {
    it('opens on Regular, so the agent proposes before it edits', async () => {
      const user = userEvent.setup();
      await renderApp();
      const form = await openNewWork(user);
      // The middle of the three: no planning session is asked for, and nothing
      // runs unattended. A level the user never chose is the one most tasks get,
      // so the default is the whole of what this asserts.
      expect(within(form).getByRole('radio', { name: 'Regular' })).toBeChecked();
      expect(within(form).getByRole('radio', { name: 'High' })).not.toBeChecked();
      expect(within(form).getByRole('radio', { name: 'None' })).not.toBeChecked();
    });

    it('reads the level a held command and mode name, not the default', async () => {
      const user = userEvent.setup();
      await renderApp();
      // Why the key had to be versioned: a held value merges over the initial
      // one, so a `command` held from a build whose default was High keeps
      // showing High for as long as the key is readable. `useRetained.test.ts`
      // covers the sweep that retires an older version's keys.
      localStorage.setItem(
        retainedKey.newWork(),
        JSON.stringify({ command: 'plan-task', taskMode: 'normal' }),
      );
      const form = await openNewWork(user);
      expect(within(form).getByRole('radio', { name: 'High' })).toBeChecked();
      expect(within(form).getByRole('radio', { name: 'Regular' })).not.toBeChecked();
    });

    /** Save the prose under `level` and return the body the form posted. */
    async function saveAtLevel(level: string) {
      const user = userEvent.setup();
      const { server } = await renderApp();
      const form = await openNewWork(user);
      await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
      await user.click(within(form).getByRole('radio', { name: level }));
      await user.click(within(form).getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
      const create = server.requests.find((r) => r.method === 'POST' && r.path === '/api/tasks');
      return create!.body as { command: string; mode: string };
    }

    it('sends the chosen level as the pair it stands for', async () => {
      // One case, not one per level: `protocol/planningLevel.test.ts` owns the
      // table. What the form has to prove is that a chosen radio reaches the
      // POST body as both fields, which is the wiring this layer can break.
      expect(await saveAtLevel('High')).toMatchObject({ command: 'plan-task', mode: 'normal' });
    });
  });

  describe('the advanced command and mode', () => {
    /** Open the dialog with Advanced unfolded. */
    async function openAdvanced(user: ReturnType<typeof userEvent.setup>) {
      const form = await openNewWork(user);
      await user.click(within(form).getByText('Advanced'));
      return form;
    }

    it('says N/A when the two fields name a pair no level stands for', async () => {
      const user = userEvent.setup();
      await renderApp();
      const form = await openAdvanced(user);
      // An execute task under `normal`: legal in the notebook, and off the map.
      await user.selectOptions(within(form).getByLabelText('Mode'), 'normal');

      expect(within(form).getByRole('radio', { name: 'N/A' })).toBeChecked();
      for (const level of ['High', 'Regular', 'None']) {
        expect(within(form).getByRole('radio', { name: level })).not.toBeChecked();
      }
    });

    it('hides N/A again once the fields name a level', async () => {
      const user = userEvent.setup();
      await renderApp();
      const form = await openAdvanced(user);
      await user.selectOptions(within(form).getByLabelText('Mode'), 'normal');
      expect(within(form).getByRole('radio', { name: 'N/A' })).toBeInTheDocument();

      // Back onto the map: the pair reads as a level, so N/A has nothing to say.
      await user.selectOptions(within(form).getByLabelText('Mode'), 'auto');
      expect(within(form).getByRole('radio', { name: 'None' })).toBeChecked();
      expect(within(form).queryByRole('radio', { name: 'N/A' })).toBeNull();
    });

    it('re-reads the level when the command changes to one a level names', async () => {
      const user = userEvent.setup();
      await renderApp();
      const form = await openAdvanced(user);
      await user.selectOptions(within(form).getByLabelText('Mode'), 'normal');
      // `plan-task` under `normal` is High, so editing the command alone lands
      // the level: the radio and the two fields are one value, not two.
      await user.type(within(form).getByLabelText('Command'), 'plan-task');

      await waitFor(() => expect(within(form).getByRole('radio', { name: 'High' })).toBeChecked());
    });

    it('writes both fields when a level is chosen', async () => {
      const user = userEvent.setup();
      await renderApp();
      const form = await openAdvanced(user);
      await user.click(within(form).getByRole('radio', { name: 'High' }));

      // Choosing a level is what sets the pair, so Advanced shows what will be
      // written rather than a stale default.
      expect(within(form).getByLabelText('Command')).toHaveValue('plan-task');
      expect(within(form).getByLabelText('Mode')).toHaveValue('normal');
    });
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

    const png = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', {
      type: 'image/png',
    });
    // One attach control on this surface: the prose field *is* the task's
    // content, so there is no second field to ask for the same text twice.
    await user.upload(within(form).getByLabelText('Attach image', { selector: 'input' }), png);
    const prose = within(form).getByLabelText('What needs doing?');
    await waitFor(() => expect((prose as HTMLTextAreaElement).value).toContain('![shot.png]('));
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
