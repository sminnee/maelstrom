import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import type { AppButtonProps } from './AppButton';
import { Spinner } from './Spinner';
import { useAnchorName } from './useAnchorName';
import { useClickLifecycle } from './useClickLifecycle';
import buttonStyles from './AppButton.module.css';
import confirmStyles from './ConfirmButton.module.css';
import styles from './SplitButton.module.css';

export interface SplitOption {
  /** The item's whole text, and the main segment's when it is the first option. */
  label: string;
  /** Drawn before the label, on the main segment and the item. Decorative. */
  icon?: ReactNode;
  /** Shown on the main segment while this option runs. Defaults to `label`. */
  processing?: ReactNode;
  /** A second line under the item. A disabled item says why here. */
  detail?: ReactNode;
  disabled?: boolean;
  /** Ask this first, beside the control. Only the `confirm` answer runs the option. */
  confirm?: { question: string; confirm: ReactNode };
  run: () => Promise<unknown>;
}

/**
 * A button with a menu of longer ways to do the same thing.
 *
 * A click on the main segment runs `options[0]`. The chevron opens a menu of
 * every option, the first included, so the menu reads as the full list. One
 * option draws a plain button with no chevron.
 *
 * The whole control has one click lifecycle, as `AppButton` does: whichever
 * option runs, its progress and its failure show on the main segment. No click
 * reaches the element behind the control.
 *
 * The menu is a `popover="auto"`, so a click outside or Escape closes it. It is
 * anchored as every popover here is — see `anchoredPopover.module.css`.
 *
 * An option with a `confirm` opens a question in the menu's place, drawn as
 * `ConfirmButton` draws its own. Its answer runs the option through the same
 * lifecycle, so the progress still shows on the main segment. Both segments
 * are held while the question is open, so no second option can start under it.
 */
export function SplitButton({
  options,
  menuLabel = 'More actions',
  variant = 'plain',
  errorResetMs,
  onError,
}: {
  options: SplitOption[];
  /** The chevron's accessible name. A row with two split buttons gives each its own. */
  menuLabel?: string;
} & Pick<AppButtonProps, 'variant' | 'errorResetMs' | 'onError'>) {
  const { state, run } = useClickLifecycle({ errorResetMs, onError });
  const [running, setRunning] = useState<SplitOption | null>(null);
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState<SplitOption | null>(null);
  const openRef = useRef(false);
  const openAtPress = useRef<boolean | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const chevronRef = useRef<HTMLButtonElement>(null);
  const { anchorStyle } = useAnchorName();
  const menuId = useId();
  const main = options[0]!;
  const processing = state.kind === 'processing';
  const split = options.length > 1;

  const items = () =>
    Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);

  // The popover's own events are the truth for `open`: a light dismiss closes
  // it without any handler here running.
  useEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const onToggle = (e: Event) => {
      const isOpen = (e as Event & { newState: string }).newState === 'open';
      openRef.current = isOpen;
      setOpen(isOpen);
      if (isOpen)
        items()
          .find((i) => i.getAttribute('aria-disabled') !== 'true')
          ?.focus();
    };
    menu.addEventListener('toggle', onToggle);
    return () => menu.removeEventListener('toggle', onToggle);
  }, [split]);

  const hide = () => {
    if (openRef.current) menuRef.current?.hidePopover();
  };

  const choose = (option: SplitOption) => {
    if (option.confirm) {
      setAsking(option);
      return;
    }
    go(option);
  };

  const go = (option: SplitOption) => {
    setAsking(null);
    setRunning(option);
    void run(option.run);
  };

  // As in `ConfirmButton`: the node exists only while asking, so it is always
  // freshly mounted here and never already open.
  const showAsk = useCallback((el: HTMLDivElement | null) => {
    el?.showPopover();
  }, []);

  const onMenuKey = (e: React.KeyboardEvent) => {
    const all = items();
    const at = all.indexOf(document.activeElement as HTMLElement);
    const to =
      e.key === 'ArrowDown'
        ? (at + 1) % all.length
        : e.key === 'ArrowUp'
          ? (at - 1 + all.length) % all.length
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? all.length - 1
              : null;
    if (to !== null) {
      e.preventDefault();
      all[to]?.focus();
    } else if (e.key === 'Escape') {
      // The Escape is the menu's: a card that collapses on Escape must not
      // hear it too.
      e.preventDefault();
      e.stopPropagation();
      hide();
      chevronRef.current?.focus();
    }
  };

  const shown = processing ? (running?.processing ?? running?.label) : main.label;
  const segment = [buttonStyles.button, buttonStyles[variant]].join(' ');

  return (
    <span
      className={styles.wrap}
      data-split={split || undefined}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className={`${segment} ${styles.main}`}
        disabled={main.disabled || processing || asking !== null}
        aria-busy={processing || undefined}
        data-state={state.kind}
        title={state.kind === 'error' ? state.message : undefined}
        onClick={() => choose(main)}
      >
        {processing ? (
          <>
            <Spinner />
            {shown}
          </>
        ) : state.kind === 'error' ? (
          <span role="alert">Failed</span>
        ) : (
          <>
            {main.icon}
            {main.label}
          </>
        )}
      </button>
      {split && (
        <>
          <button
            ref={chevronRef}
            type="button"
            className={`${segment} ${styles.chevron}`}
            style={anchorStyle}
            aria-label={menuLabel}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-controls={menuId}
            disabled={processing || asking !== null}
            // A click outside an open popover closes it before `click` fires,
            // so the chevron reads the state as the press began: a click that
            // closed the menu must not open it again.
            onPointerDown={() => {
              openAtPress.current = openRef.current;
            }}
            onClick={() => {
              const wasOpen = openAtPress.current ?? openRef.current;
              openAtPress.current = null;
              if (wasOpen) hide();
              else menuRef.current?.showPopover();
            }}
          >
            <span aria-hidden="true">▾</span>
          </button>
          <div
            ref={menuRef}
            id={menuId}
            className={styles.menu}
            style={anchorStyle}
            role="menu"
            popover="auto"
            onKeyDown={onMenuKey}
          >
            {options.map((option, i) => {
              const detailId = `${menuId}-${i}`;
              return (
                <button
                  key={option.label}
                  type="button"
                  role="menuitem"
                  className={styles.item}
                  aria-label={option.label}
                  aria-describedby={option.detail ? detailId : undefined}
                  aria-disabled={option.disabled || undefined}
                  tabIndex={-1}
                  onClick={() => {
                    if (option.disabled) return;
                    hide();
                    chevronRef.current?.focus();
                    choose(option);
                  }}
                >
                  <span className={styles.label}>
                    {option.icon}
                    {option.label}
                  </span>
                  {option.detail && (
                    <span id={detailId} className={styles.detail}>
                      {option.detail}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </>
      )}
      {asking?.confirm && (
        <div
          ref={showAsk}
          className={confirmStyles.ask}
          style={anchorStyle}
          role="alertdialog"
          aria-label={asking.confirm.question}
          popover="manual"
        >
          <span>{asking.confirm.question}</span>
          <button type="button" onClick={() => setAsking(null)}>
            Keep it
          </button>
          <button
            type="button"
            className={[buttonStyles.button, buttonStyles.primary].join(' ')}
            onClick={() => go(asking)}
          >
            {asking.confirm.confirm}
          </button>
        </div>
      )}
    </span>
  );
}
