import { describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { act } from 'react';
import { commandsSince } from './test/appHelpers';
import { renderApp } from './test/renderApp';

/** The seed holds work of two projects, so its board draws lanes. One project draws none. */
const ONE_PROJECT = '/desk/kanban?project=northwind';

const column = (name: string) => screen.getByRole('region', { name });
const board = () => within(screen.getByTestId('desk-kanban'));
const lane = (name: string) => board().getByRole('group', { name });
/** The cell of zone `name` in the lane of project `project`. */
const cell = (project: string, name: string) =>
  within(lane(project)).getByRole('region', { name: `${name}, ${project}` });
const card = (id: string) =>
  document.querySelector<HTMLElement>(`[data-testid="deck-row"][data-task-id="${id}"]`)!;

/** jsdom has no drag: a stub carries the data the way a browser would. */
function dataTransfer() {
  const data = new Map<string, string>();
  return {
    setData: (type: string, value: string) => data.set(type, value),
    getData: (type: string) => data.get(type) ?? '',
    dropEffect: 'none',
    effectAllowed: 'all',
  };
}

describe('the desk modes', () => {
  it('redirects the desk to the graph, keeping the search', async () => {
    const { router } = await renderApp({ url: '/desk?project=northwind' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/desk/graph'));
    expect(router.state.location.search).toBe('?project=northwind');
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
  });

  it('redirects an old card path to the same card in the graph', async () => {
    const { router } = await renderApp({ url: '/desk/task/NORT-12' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/desk/graph/task/NORT-12'));
    expect(await screen.findByRole('dialog', { name: /Rotate auth tokens/ })).toBeInTheDocument();
  });

  it('switches the drawn view and the path', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp();
    const modes = screen.getByRole('navigation', { name: 'Desk mode' });
    await user.click(within(modes).getByRole('link', { name: 'Kanban' }));
    expect(router.state.location.pathname).toBe('/desk/kanban');
    expect(screen.getByTestId('desk-kanban')).toBeInTheDocument();
    expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();
    await user.click(within(modes).getByRole('link', { name: 'List' }));
    expect(router.state.location.pathname).toBe('/desk/list');
    expect(screen.getByTestId('deck-list')).toBeInTheDocument();
  });

  it('draws one column per zone in board order, each with its count', async () => {
    await renderApp({ url: ONE_PROJECT });
    const heads = screen.getAllByRole('region').map((r) => r.getAttribute('aria-label'));
    expect(heads).toEqual(['Queued', 'To do', 'Running', 'Done']);
    // NORT-20 follows nothing, so it is the one task ready to start.
    expect(within(column('To do')).getByTestId('zone-count')).toHaveTextContent('1');
    expect(within(column('To do')).getAllByTestId('deck-row')).toHaveLength(1);
    expect(board().queryByRole('group')).not.toBeInTheDocument();
  });

  it('draws a lane per project across the zone columns, with work of two projects', async () => {
    await renderApp({ url: '/desk/kanban' });
    const lanes = board()
      .getAllByRole('group')
      .map((g) => g.getAttribute('aria-label'));
    expect(lanes).toEqual(['maelstrom', 'northwind']);
    // The zone heads draw once, over all the lanes.
    expect(
      board()
        .getAllByRole('heading', { level: 2 })
        .map((h) => h.textContent),
    ).toEqual([
      expect.stringMatching(/^Queued/),
      expect.stringMatching(/^To do/),
      expect.stringMatching(/^Running/),
      expect.stringMatching(/^Done/),
    ]);
    for (const name of lanes) {
      const zones = within(lane(name!)).getAllByRole('region');
      expect(zones.map((r) => r.getAttribute('aria-label'))).toEqual(
        ['Queued', 'To do', 'Running', 'Done'].map((zone) => `${zone}, ${name}`),
      );
    }
    expect(within(cell('northwind', 'To do')).getAllByTestId('deck-row')).toHaveLength(1);
  });

  it('sets the status a drop on Queued puts the card in', async () => {
    const { server } = await renderApp({ url: '/desk/kanban' });
    const before = server.requests.length;
    const transfer = dataTransfer();
    fireEvent.dragStart(card('NORT-20'), { dataTransfer: transfer });
    fireEvent.dragOver(cell('northwind', 'Queued'), { dataTransfer: transfer });
    // Only the cell the drop would land in lights.
    expect(cell('northwind', 'Queued')).toHaveAttribute('data-drop');
    expect(
      board()
        .getAllByRole('region')
        .filter((r) => r.hasAttribute('data-drop')),
    ).toHaveLength(1);
    fireEvent.drop(cell('northwind', 'Queued'), { dataTransfer: transfer });
    fireEvent.dragEnd(card('NORT-20'), { dataTransfer: transfer });
    await waitFor(() =>
      expect(commandsSince(server, before)).toEqual(['POST /api/tasks/NORT-20/status']),
    );
    const write = server.requests.slice(before).find((r) => r.method === 'POST');
    expect(write?.body).toEqual({ status: 'blocked' });
    // The fake reads `actionable` fresh, as the server does, so the card lands where it was dropped.
    await waitFor(() =>
      expect(within(cell('northwind', 'Queued')).getByText(/Add a search/)).toBeInTheDocument(),
    );
  });

  it('refuses a drop in another project’s lane: a drop cannot move a task', async () => {
    const { server } = await renderApp({ url: '/desk/kanban' });
    const before = server.requests.length;
    const transfer = dataTransfer();
    fireEvent.dragStart(card('NORT-20'), { dataTransfer: transfer });
    fireEvent.dragOver(cell('maelstrom', 'Queued'), { dataTransfer: transfer });
    expect(cell('maelstrom', 'Queued')).not.toHaveAttribute('data-drop');
    fireEvent.drop(cell('maelstrom', 'Queued'), { dataTransfer: transfer });
    fireEvent.dragEnd(card('NORT-20'), { dataTransfer: transfer });
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(commandsSince(server, before)).toEqual([]);
  });

  it('refuses a drop on Running: a drop never launches', async () => {
    const { server } = await renderApp({ url: ONE_PROJECT });
    const before = server.requests.length;
    const transfer = dataTransfer();
    fireEvent.dragStart(card('NORT-20'), { dataTransfer: transfer });
    fireEvent.dragOver(column('Running'), { dataTransfer: transfer });
    expect(column('Running')).not.toHaveAttribute('data-drop');
    fireEvent.drop(column('Running'), { dataTransfer: transfer });
    fireEvent.dragEnd(card('NORT-20'), { dataTransfer: transfer });
    // A write goes out after an await, so let one land before asserting there is none.
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(commandsSince(server, before)).toEqual([]);
  });

  it('opens a clicked card at the slot edge, on the mode’s own path', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp({ url: '/desk/kanban' });
    await user.click(within(card('NORT-20')).getByRole('link'));
    expect(router.state.location.pathname).toBe('/desk/kanban/task/NORT-20');
    const dialog = screen.getByTestId('slot-card');
    expect(dialog).toHaveAccessibleName(/Add a search to the orders table/);
    await user.keyboard('{Escape}');
    expect(router.state.location.pathname).toBe('/desk/kanban');
    expect(screen.queryByTestId('slot-card')).not.toBeInTheDocument();
  });
});
