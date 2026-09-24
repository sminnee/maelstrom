import { useCallback, type ReactNode } from 'react';
import { AppButton, type AppButtonProps, type ButtonClickHandler } from './AppButton';
import { useAnchorName } from './useAnchorName';
import styles from './ConfirmButton.module.css';

/**
 * A button that asks before it acts.
 *
 * The question opens beside the button rather than over the page: a destructive
 * action in a table row or a dialog footer is answered where it was asked, and
 * nothing behind it moves. Only the answer runs `onConfirm`, so a caller never
 * sees an unconfirmed click.
 *
 * The question is a popover, so it draws in the top layer and no scrolling
 * ancestor clips it — a row low in the task list would otherwise have its
 * question cut off by the list's own box. `anchoredPopover.module.css` carries
 * the two rules that place it.
 *
 * The caller owns whether the question is open, because a list shows one at a
 * time: two rows asking at once is two destructive actions one click apart.
 */
export function ConfirmButton({
  question,
  confirm,
  cancel = 'Keep it',
  asking,
  onAsk,
  onDismiss,
  onConfirm,
  onError,
  children,
  ...rest
}: {
  /** What is asked, e.g. "Delete this task?". */
  question: string;
  /** The word on the button that goes ahead, e.g. "Delete it". */
  confirm: ReactNode;
  cancel?: ReactNode;
  asking: boolean;
  onAsk: () => void;
  onDismiss: () => void;
  onConfirm: ButtonClickHandler;
  children: ReactNode;
} & Omit<AppButtonProps, 'onClick' | 'children'>) {
  const { anchorStyle } = useAnchorName();

  // A popover is inert until it is shown, and React renders the attribute
  // rather than calling the method. The node only exists while `asking`, so it
  // is always freshly mounted here and never already open.
  const open = useCallback((el: HTMLDivElement | null) => {
    el?.showPopover();
  }, []);

  return (
    <span className={styles.wrap}>
      <AppButton {...rest} className={styles.trigger} style={anchorStyle} onClick={onAsk}>
        {children}
      </AppButton>
      {asking && (
        <div
          ref={open}
          className={styles.ask}
          style={anchorStyle}
          role="alertdialog"
          aria-label={question}
          popover="manual"
        >
          <span>{question}</span>
          <button type="button" onClick={onDismiss}>
            {cancel}
          </button>
          {/* The action runs here, so a failure is this button's to report --
              `onError` on the trigger would never hear it. */}
          <AppButton
            variant="primary"
            onError={onError}
            onClick={async (e) => {
              await onConfirm(e);
              onDismiss();
            }}
          >
            {confirm}
          </AppButton>
        </div>
      )}
    </span>
  );
}
