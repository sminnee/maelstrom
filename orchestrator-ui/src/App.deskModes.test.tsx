import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp } from './test/renderApp';

const card = (id: string) =>
  document.querySelector<HTMLElement>(`[data-testid="deck-row"][data-task-id="${id}"]`)!;

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
    await user.click(within(modes).getByRole('link', { name: 'List' }));
    expect(router.state.location.pathname).toBe('/desk/list');
    expect(screen.getByTestId('deck-list')).toBeInTheDocument();
    expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();
  });

  it('opens a clicked row at the slot edge, on the mode’s own path', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp({ url: '/desk/list?zone=todo' });
    await user.click(within(card('NORT-20')).getByRole('link'));
    expect(router.state.location.pathname).toBe('/desk/list/task/NORT-20');
    const dialog = screen.getByTestId('slot-card');
    expect(dialog).toHaveAccessibleName(/Add a search to the orders table/);
    await user.keyboard('{Escape}');
    expect(router.state.location.pathname).toBe('/desk/list');
    expect(screen.queryByTestId('slot-card')).not.toBeInTheDocument();
  });
});
