import { useCallback, useEffect, useRef } from 'react';
import styles from './Dialog.module.css';

/**
 * The app's one modal shell: a native `<dialog>`, opened modally, and one way
 * out that Escape and a click on the backdrop both take.
 *
 * It owns nothing but the shell. What "leaving" means — closing at once, or
 * asking about unsaved work first — belongs to the caller, which is why
 * `onClose` is a callback rather than a piece of state here.
 *
 * `showModal()` is what puts it in the top layer, so a combo box offer inside
 * it can draw over the dialog rather than being clipped by it. It also traps
 * the focus and draws the `::backdrop`, both of which this used to do by hand
 * or not at all.
 */
export function Dialog({
  label,
  onClose,
  testId,
  className,
  children,
}: {
  /** The dialog's accessible name. */
  label: string;
  /** Escape, a click on the backdrop, or the header's ×. */
  onClose: () => void;
  testId?: string;
  /** Added to the box, for a dialog whose content is not text. */
  className?: string;
  children: React.ReactNode;
}) {
  const box = useRef<HTMLDialogElement>(null);

  // Open from a callback ref, not an effect: `showModal()` throws on a dialog
  // that is already open, and a callback ref fires only when the DOM node
  // changes rather than on every re-render of the parent.
  const open = useCallback((el: HTMLDialogElement | null) => {
    box.current = el;
    if (el && !el.open) el.showModal();
  }, []);

  useEffect(() => {
    // Where focus was before the dialog took it. Restoring it on the way out
    // is what lets a keyboard user carry on from where they were, rather than
    // landing on the body and tabbing from the top of the document.
    //
    // The browser does this itself for a dialog that is closed, but every
    // caller here unmounts it instead, and a removed element restores nothing.
    const opener = document.activeElement;
    // `showModal()` focuses the first field inside. Focus the box instead, so a
    // screen reader reads the dialog from its start.
    box.current?.focus({ preventScroll: true });
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected)
        opener.focus({ preventScroll: true });
    };
  }, []);

  return (
    <dialog
      ref={open}
      className={[styles.dialog, className].filter(Boolean).join(' ')}
      aria-label={label}
      data-testid={testId}
      tabIndex={-1}
      // Escape reaches the dialog as `cancel`. Taking it here rather than on the
      // document is what lets a control inside stop the key first -- the combo
      // box does, so one press dismisses its offer and not the dialog too.
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      // A modal dialog fills the viewport, so a press on the backdrop lands on
      // the element itself. A press on the content lands on a child.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {children}
    </dialog>
  );
}

/** The dialog's title row, with the × that closes it. */
export function DialogHeader({ title, onClose }: { title: string; onClose: () => void }) {
  return (
    <header className={styles.header}>
      <h2 className={styles.heading}>{title}</h2>
      <button type="button" className={styles.close} aria-label="Close" onClick={onClose}>
        ×
      </button>
    </header>
  );
}

/** The right-aligned button row a dialog ends with. */
export function DialogFooter({ children }: { children: React.ReactNode }) {
  return <footer className={styles.footer}>{children}</footer>;
}
