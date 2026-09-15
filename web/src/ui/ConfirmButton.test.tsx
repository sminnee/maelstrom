import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { ConfirmButton } from './ConfirmButton';

/** The component as a caller holds it: the caller owns whether it is asking. */
function Harness({ onConfirm }: { onConfirm: () => void | Promise<unknown> }) {
  const [asking, setAsking] = useState(false);
  return (
    <ConfirmButton
      question="Delete this task?"
      confirm="Delete it"
      asking={asking}
      onAsk={() => setAsking(true)}
      onDismiss={() => setAsking(false)}
      onConfirm={onConfirm}
    >
      Delete
    </ConfirmButton>
  );
}

describe('ConfirmButton', () => {
  it('asks rather than acting on the first click', () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    expect(screen.getByRole('alertdialog', { name: 'Delete this task?' })).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('acts once the question is answered', async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Delete it' }));
    });

    expect(onConfirm).toHaveBeenCalledOnce();
    // The question closes itself, so the caller does not have to.
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('dismisses without acting', () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    fireEvent.click(screen.getByRole('button', { name: 'Keep it' }));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    // The trigger comes back, so the action can be asked for again.
    expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
  });

  it('leaves the question open when the action fails', async () => {
    const onConfirm = vi.fn(() => Promise.reject(new Error('refused')));
    render(<Harness onConfirm={onConfirm} />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Delete it' }));
    });

    // `AppButton` owns the failure, and the question stays so it can be
    // retried rather than silently dropping the user back to the trigger.
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  });
});
