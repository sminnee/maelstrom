import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { AppButton } from './AppButton';

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

describe('AppButton', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('stays ready through a synchronous handler', () => {
    const onClick = vi.fn();
    render(<AppButton onClick={onClick}>Go</AppButton>);
    const button = screen.getByRole('button', { name: 'Go' });
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute('data-state', 'ready');
  });

  it('is disabled and busy with a spinner while the handler is pending, then ready again', async () => {
    const pending = deferred();
    render(
      <AppButton onClick={() => pending.promise} processingChildren="Sending">
        Send
      </AppButton>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    expect(button).toHaveAttribute('data-state', 'processing');
    expect(button).toHaveTextContent('Sending');
    expect(button.querySelector('[data-testid="spinner"]')).not.toBeNull();

    await act(async () => pending.resolve());
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute('data-state', 'ready');
    expect(button).toHaveTextContent('Send');
  });

  it('shows the failure, reports it, and resets after errorResetMs', async () => {
    const onError = vi.fn();
    render(
      <AppButton onClick={() => Promise.reject(new Error('stale_request'))} onError={onError}>
        Approve
      </AppButton>,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    });
    const button = screen.getByRole('button');
    expect(button).toHaveAttribute('data-state', 'error');
    expect(button).toHaveTextContent('Failed');
    expect(button).toHaveAttribute('title', 'stale_request');
    expect(screen.getByRole('alert')).toHaveTextContent('Failed');
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: 'stale_request' }));
    expect(button).toBeEnabled();

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(button).toHaveAttribute('data-state', 'ready');
    expect(button).toHaveTextContent('Approve');
  });

  it('retries on a click while showing the error', async () => {
    const onClick = vi.fn().mockRejectedValueOnce(new Error('no')).mockResolvedValueOnce(undefined);
    render(
      <AppButton onClick={onClick} errorResetMs={0}>
        Approve
      </AppButton>,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('button'));
    });
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'error');
    await act(async () => {
      fireEvent.click(screen.getByRole('button'));
    });
    expect(onClick).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'ready');
  });

  it('treats a handler that throws synchronously as a failure too', () => {
    const onError = vi.fn();
    render(
      <AppButton
        onClick={() => {
          throw new Error('boom');
        }}
        onError={onError}
      >
        Go
      </AppButton>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'error');
    expect(screen.getByRole('button')).toHaveAttribute('title', 'boom');
    expect(onError).toHaveBeenCalledOnce();
  });

  it('survives an unmount while the handler is pending', async () => {
    const pending = deferred();
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { unmount } = render(<AppButton onClick={() => pending.promise}>Go</AppButton>);
    fireEvent.click(screen.getByRole('button'));
    unmount();
    await act(async () => pending.resolve());
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it('does not let the click reach the element behind it', () => {
    const behind = vi.fn();
    render(
      <div onClick={behind}>
        <AppButton onClick={() => undefined}>Go</AppButton>
      </div>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(behind).not.toHaveBeenCalled();
  });
});
