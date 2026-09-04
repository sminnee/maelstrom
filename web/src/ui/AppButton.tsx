import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Spinner } from './Spinner';
import styles from './AppButton.module.css';

export type ButtonClickHandler = (
  e: React.MouseEvent<HTMLButtonElement>,
) => void | Promise<unknown>;

export interface AppButtonProps extends Omit<
  React.ButtonHTMLAttributes<HTMLButtonElement>,
  'onClick'
> {
  ref?: React.Ref<HTMLButtonElement>;
  onClick?: ButtonClickHandler;
  variant?: 'plain' | 'primary' | 'quiet';
  /** Shown beside the spinner while the handler is pending. Defaults to `children`. */
  processingChildren?: ReactNode;
  /** Shown after the handler rejects. Defaults to "Failed"; the message goes in `title`. */
  errorChildren?: ReactNode | ((err: unknown) => ReactNode);
  /** How long the error shows before the button is ready again. `0` holds it until the next click. */
  errorResetMs?: number;
  onError?: (err: unknown) => void;
}

type State =
  { kind: 'ready' } | { kind: 'processing' } | { kind: 'error'; message: string; error: unknown };

const READY: State = { kind: 'ready' };

/**
 * A button that owns the life of its click: `processing` while a returned
 * promise is pending, `error` for `errorResetMs` after it rejects, `ready`
 * otherwise. See `docs/dev/orchestrator-ui.md`, "Commands are mutations".
 *
 * The click never reaches the element behind the button, so a button on a
 * canvas node does not also toggle the node. The rejection is not rethrown:
 * React reports a rejection from an async handler as unhandled. `onError`
 * hears it instead.
 */
export function AppButton({
  onClick,
  variant = 'plain',
  processingChildren,
  errorChildren = 'Failed',
  errorResetMs = 3000,
  onError,
  children,
  className,
  disabled,
  title,
  type = 'button',
  ...rest
}: AppButtonProps) {
  const [state, setState] = useState<State>(READY);
  const mounted = useRef(true);
  const reset = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (reset.current) clearTimeout(reset.current);
    };
  }, []);

  const clearReset = () => {
    if (reset.current) clearTimeout(reset.current);
    reset.current = null;
  };

  const handleClick = async (e: React.MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (!onClick) return;
    clearReset();
    let result: void | Promise<unknown>;
    try {
      result = onClick(e);
    } catch (err) {
      fail(err);
      return;
    }
    if (!isThenable(result)) {
      if (state.kind !== 'ready') setState(READY);
      return;
    }
    setState({ kind: 'processing' });
    try {
      await result;
      if (mounted.current) setState(READY);
    } catch (err) {
      fail(err);
    }
  };

  const fail = (err: unknown) => {
    onError?.(err);
    if (!mounted.current) return;
    setState({ kind: 'error', message: messageOf(err), error: err });
    if (errorResetMs > 0) {
      reset.current = setTimeout(() => {
        reset.current = null;
        if (mounted.current) setState(READY);
      }, errorResetMs);
    }
  };

  const classes = [styles.button, styles[variant], className].filter(Boolean).join(' ');
  return (
    <button
      {...rest}
      type={type}
      className={classes}
      disabled={disabled || state.kind === 'processing'}
      aria-busy={state.kind === 'processing' || undefined}
      data-state={state.kind}
      title={state.kind === 'error' ? state.message : title}
      onClick={(e) => void handleClick(e)}
    >
      {state.kind === 'processing' ? (
        <>
          <Spinner />
          {processingChildren ?? children}
        </>
      ) : state.kind === 'error' ? (
        <span role="alert">
          {typeof errorChildren === 'function' ? errorChildren(state.error) : errorChildren}
        </span>
      ) : (
        children
      )}
    </button>
  );
}

function isThenable(value: unknown): value is Promise<unknown> {
  return !!value && typeof (value as Promise<unknown>).then === 'function';
}

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
