import type { ReactNode } from 'react';
import { Spinner } from './Spinner';
import { useClickLifecycle } from './useClickLifecycle';
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
  variant?: 'plain' | 'primary' | 'quiet' | 'link';
  /** Shown beside the spinner while the handler is pending. Defaults to `children`. */
  processingChildren?: ReactNode;
  /** Shown after the handler rejects. Defaults to "Failed"; the message goes in `title`. */
  errorChildren?: ReactNode | ((err: unknown) => ReactNode);
  /** Passed to `useClickLifecycle`. */
  errorResetMs?: number;
  onError?: (err: unknown) => void;
}

/**
 * A button that owns the life of its click — see `useClickLifecycle`, and
 * `docs/dev/orchestrator-ui.md`, "Commands are mutations".
 *
 * The click never reaches the element behind the button, so a button on a
 * canvas node does not also toggle the node.
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
  const { state, run } = useClickLifecycle({ errorResetMs, onError });

  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (!onClick) return;
    void run(() => onClick(e));
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
      onClick={handleClick}
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
