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
  variant?: 'plain' | 'primary' | 'danger' | 'quiet' | 'link';
  /**
   * Drawn before the text — one of `actionIcons.ts`. Decorative; the text
   * stays the name. On the narrow layout the text is a caption under it.
   */
  icon?: ReactNode;
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
  icon,
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

  // A label that wraps makes a pill a blob: the row wraps, not the label. A link
  // is text and follows the Wide Content Rule (DESIGN.md).
  const pill = variant !== 'link';
  const label = (content: ReactNode) =>
    pill && textOf(content) !== undefined ? <span className="truncate">{content}</span> : content;
  const classes = [styles.button, styles[variant], pill ? 'nowrap' : null, className]
    .filter(Boolean)
    .join(' ');
  return (
    <button
      {...rest}
      type={type}
      className={classes}
      disabled={disabled || state.kind === 'processing'}
      aria-busy={state.kind === 'processing' || undefined}
      data-state={state.kind}
      data-icon={icon === undefined ? undefined : ''}
      title={
        state.kind === 'error' ? state.message : (title ?? (pill ? textOf(children) : undefined))
      }
      onClick={handleClick}
    >
      {state.kind === 'processing' ? (
        <>
          <Spinner />
          {label(processingChildren ?? children)}
        </>
      ) : state.kind === 'error' ? (
        <span role="alert" className="truncate">
          {typeof errorChildren === 'function' ? errorChildren(state.error) : errorChildren}
        </span>
      ) : (
        <>
          {icon}
          {label(children)}
        </>
      )}
    </button>
  );
}

/** The label as text, when it is only text: a pill cuts it, and `title` holds it whole. */
function textOf(children: ReactNode): string | undefined {
  const parts = Array.isArray(children) ? children : [children];
  if (!parts.every((p) => typeof p === 'string' || typeof p === 'number')) return undefined;
  const text = parts.join('');
  return text === '' ? undefined : text;
}
