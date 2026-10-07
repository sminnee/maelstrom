import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SplitButton, type SplitOption } from './SplitButton';

/** A promise the test settles by hand. */
function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function three(over: Partial<Record<number, Partial<SplitOption>>> = {}): SplitOption[] {
  return [
    { label: 'Terminate', run: vi.fn(() => Promise.resolve()), ...over[0] },
    { label: 'Terminate & take off desk', run: vi.fn(() => Promise.resolve()), ...over[1] },
    {
      label: 'Terminate, take off desk & close alpha',
      run: vi.fn(() => Promise.resolve()),
      ...over[2],
    },
  ];
}

const chevron = () => screen.getByRole('button', { name: 'More actions' });

describe('SplitButton', () => {
  it('runs the first option on a main click, and no other', async () => {
    const user = userEvent.setup();
    const options = three();
    render(<SplitButton options={options} />);
    await user.click(screen.getByRole('button', { name: 'Terminate' }));
    expect(options[0]!.run).toHaveBeenCalledOnce();
    expect(options[1]!.run).not.toHaveBeenCalled();
    expect(options[2]!.run).not.toHaveBeenCalled();

    // With none marked, the first is the default, and its item says so.
    await user.click(chevron());
    expect(screen.getByRole('menuitem', { name: 'Terminate' })).toHaveAttribute('data-default');
    expect(screen.getByRole('menuitem', { name: 'Terminate & take off desk' })).not.toHaveAttribute(
      'data-default',
    );
  });

  it('runs the default option on a main click, under its button label, and marks its item', async () => {
    const user = userEvent.setup();
    const options = three({ 2: { isDefault: true, buttonLabel: 'Dismiss' } });
    render(<SplitButton options={options} />);
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(options[2]!.run).toHaveBeenCalledOnce();
    expect(options[0]!.run).not.toHaveBeenCalled();

    await user.click(chevron());
    expect(
      screen.getByRole('menuitem', { name: 'Terminate, take off desk & close alpha' }),
    ).toHaveAttribute('data-default');
    expect(screen.getByRole('menuitem', { name: 'Terminate' })).not.toHaveAttribute('data-default');
  });

  it('opens a menu from the chevron, and a chosen item runs that item and closes it', async () => {
    const user = userEvent.setup();
    const options = three();
    render(<SplitButton options={options} />);
    expect(chevron()).toHaveAttribute('aria-haspopup', 'menu');
    expect(chevron()).toHaveAttribute('aria-expanded', 'false');

    await user.click(chevron());
    expect(chevron()).toHaveAttribute('aria-expanded', 'true');
    const menu = screen.getByRole('menu');
    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((i) => i.getAttribute('aria-label')),
    ).toEqual(['Terminate', 'Terminate & take off desk', 'Terminate, take off desk & close alpha']);
    // Opening puts the focus on the first item, so the keyboard can go on.
    expect(screen.getByRole('menuitem', { name: 'Terminate' })).toHaveFocus();

    await user.click(screen.getByRole('menuitem', { name: 'Terminate & take off desk' }));
    expect(options[1]!.run).toHaveBeenCalledOnce();
    expect(options[0]!.run).not.toHaveBeenCalled();
    expect(chevron()).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes the menu on a second chevron click', async () => {
    const user = userEvent.setup();
    render(<SplitButton options={three()} />);
    await user.click(chevron());
    await user.click(chevron());
    expect(chevron()).toHaveAttribute('aria-expanded', 'false');
  });

  it('opens with the focus on the first enabled item', async () => {
    const user = userEvent.setup();
    render(<SplitButton options={three({ 0: { disabled: true } })} />);
    await user.click(chevron());
    expect(screen.getByRole('menuitem', { name: 'Terminate & take off desk' })).toHaveFocus();
  });

  it('opens a menu that appeared when its options grew from one', async () => {
    const user = userEvent.setup();
    const options = three();
    const { rerender } = render(<SplitButton options={options.slice(0, 1)} />);
    expect(screen.queryByRole('button', { name: 'More actions' })).toBeNull();

    rerender(<SplitButton options={options} />);
    await user.click(chevron());
    expect(screen.getByRole('menuitem', { name: 'Terminate' })).toHaveFocus();
    expect(chevron()).toHaveAttribute('aria-expanded', 'true');
  });

  it('does not run a disabled item, and says why it is disabled', async () => {
    const user = userEvent.setup();
    const options = three({
      2: { disabled: true, detail: '1 other agent still running in alpha' },
    });
    render(<SplitButton options={options} />);
    await user.click(chevron());
    const item = screen.getByRole('menuitem', { name: 'Terminate, take off desk & close alpha' });
    expect(item).toHaveAttribute('aria-disabled', 'true');
    expect(item).toHaveAccessibleDescription('1 other agent still running in alpha');

    await user.click(item);
    expect(options[2]!.run).not.toHaveBeenCalled();
    // A disabled item keeps the menu open, since nothing was chosen.
    expect(chevron()).toHaveAttribute('aria-expanded', 'true');
  });

  it('moves the focus with the arrow keys, Home and End', async () => {
    const user = userEvent.setup();
    render(<SplitButton options={three()} />);
    await user.click(chevron());
    const item = (name: string) => screen.getByRole('menuitem', { name });
    await user.keyboard('{ArrowDown}');
    expect(item('Terminate & take off desk')).toHaveFocus();
    await user.keyboard('{End}');
    expect(item('Terminate, take off desk & close alpha')).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(item('Terminate')).toHaveFocus();
    await user.keyboard('{ArrowUp}');
    expect(item('Terminate, take off desk & close alpha')).toHaveFocus();
    await user.keyboard('{Home}');
    expect(item('Terminate')).toHaveFocus();
  });

  it('closes the menu on Escape and gives the focus back to the chevron', async () => {
    const user = userEvent.setup();
    // The canvas card collapses on a document-level Escape; the menu's must not reach it.
    const heard = vi.fn();
    document.addEventListener('keydown', heard);
    render(<SplitButton options={three()} />);
    await user.click(chevron());
    await user.keyboard('{Escape}');
    document.removeEventListener('keydown', heard);
    expect(chevron()).toHaveAttribute('aria-expanded', 'false');
    expect(chevron()).toHaveFocus();
    expect(heard).not.toHaveBeenCalled();
  });

  it('shows the running option on the main segment, and holds the chevron while it runs', async () => {
    const user = userEvent.setup();
    const pending = deferred();
    const options = three({ 2: { run: () => pending.promise, processing: 'Closing' } });
    render(<SplitButton options={options} />);
    await user.click(chevron());
    await user.click(
      screen.getByRole('menuitem', { name: 'Terminate, take off desk & close alpha' }),
    );

    const main = screen.getByRole('button', { name: 'Closing' });
    expect(main).toBeDisabled();
    expect(main).toHaveAttribute('aria-busy', 'true');
    expect(main.querySelector('[data-testid="spinner"]')).not.toBeNull();
    expect(chevron()).toBeDisabled();

    await act(async () => pending.resolve());
    expect(screen.getByRole('button', { name: 'Terminate' })).toBeEnabled();
    expect(chevron()).toBeEnabled();
  });

  it('shows a failure on the main segment with the message in its title', async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    const options = three({
      2: { run: () => Promise.reject(new Error('Worktree has uncommitted changes')) },
    });
    render(<SplitButton options={options} onError={onError} errorResetMs={0} />);
    await user.click(chevron());
    await user.click(
      screen.getByRole('menuitem', { name: 'Terminate, take off desk & close alpha' }),
    );

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Failed');
    const main = alert.closest('button')!;
    expect(main).toHaveAttribute('data-state', 'error');
    expect(main).toHaveAttribute('title', 'Worktree has uncommitted changes');
    expect(onError).toHaveBeenCalledOnce();
  });

  it('asks before it runs an option that needs a confirm', async () => {
    const user = userEvent.setup();
    const options = three({
      2: {
        label: 'Terminate, dismiss & trash alpha',
        confirm: { question: 'Trash feature/x?', confirm: 'Trash it' },
      },
    });
    render(<SplitButton options={options} />);
    const pick = async () => {
      await user.click(chevron());
      await user.click(screen.getByRole('menuitem', { name: 'Terminate, dismiss & trash alpha' }));
    };

    await pick();
    const ask = screen.getByRole('alertdialog', { name: 'Trash feature/x?' });
    expect(options[2]!.run).not.toHaveBeenCalled();
    // No second option can start while the question is open.
    expect(screen.getByRole('button', { name: 'Terminate' })).toBeDisabled();
    expect(chevron()).toBeDisabled();

    await user.click(within(ask).getByRole('button', { name: 'Keep it' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(options[2]!.run).not.toHaveBeenCalled();

    await pick();
    await user.click(screen.getByRole('button', { name: 'Trash it' }));
    expect(options[2]!.run).toHaveBeenCalledOnce();
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('shows the failure of a confirmed option on the main segment', async () => {
    const user = userEvent.setup();
    const options = three({
      0: {
        confirm: { question: 'Really?', confirm: 'Do it' },
        run: vi.fn(() => Promise.reject(new Error('trash/feature/x already exists'))),
      },
    });
    render(<SplitButton options={options} />);

    await user.click(screen.getByRole('button', { name: 'Terminate' }));
    await user.click(screen.getByRole('button', { name: 'Do it' }));
    const alert = await screen.findByRole('alert');
    expect(alert.closest('button')).toHaveAttribute('title', 'trash/feature/x already exists');
  });

  it('draws a plain button with no chevron when there is one option', () => {
    render(<SplitButton options={three().slice(0, 1)} />);
    expect(screen.getByRole('button', { name: 'Terminate' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
  });

  it('draws an option icon on the main segment and its menu item, and the label stays the name', async () => {
    const user = userEvent.setup();
    const icon = <svg data-testid="icon" aria-hidden="true" />;
    render(<SplitButton options={three({ 0: { icon } })} />);
    const main = screen.getByRole('button', { name: 'Terminate' });
    expect(within(main).getByTestId('icon')).toBeInTheDocument();

    await user.click(chevron());
    const item = screen.getByRole('menuitem', { name: 'Terminate' });
    expect(within(item).getByTestId('icon')).toBeInTheDocument();
    expect(
      within(screen.getByRole('menuitem', { name: 'Terminate & take off desk' })).queryByTestId(
        'icon',
      ),
    ).toBeNull();
  });

  it('names the chevron with menuLabel, so two controls in one row stay apart', () => {
    render(<SplitButton options={three()} menuLabel="More sync actions" />);
    expect(screen.getByRole('button', { name: 'More sync actions' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
  });

  it('keeps its clicks from reaching the element behind it', async () => {
    const behind = vi.fn();
    render(
      <div onClick={behind}>
        <SplitButton options={three()} />
      </div>,
    );
    // Each run settles before the next click, since the chevron holds while one runs.
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Terminate' })));
    fireEvent.click(chevron());
    await act(async () =>
      fireEvent.click(screen.getByRole('menuitem', { name: 'Terminate & take off desk' })),
    );
    expect(behind).not.toHaveBeenCalled();
  });
});
