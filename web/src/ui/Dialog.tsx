import { useEffect, useRef } from 'react';
import styles from './Dialog.module.css';

/**
 * The app's one modal shell: a scrim, a focused `role="dialog"` box, and one
 * way out that Escape and a click on the scrim both take.
 *
 * It owns nothing but the shell. What "leaving" means — closing at once, or
 * asking about unsaved work first — belongs to the caller, which is why
 * `onClose` is a callback rather than a piece of state here.
 *
 * Focus moves into the box on mount and back to whatever held it on unmount.
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
  /** Escape, a click on the scrim, or the header's ×. */
  onClose: () => void;
  testId?: string;
  /** Added to the box, for a dialog whose content is not text. */
  className?: string;
  children: React.ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Where focus was before the dialog took it. Restoring it on the way out
    // is what lets a keyboard user carry on from where they were, rather than
    // landing on the body and tabbing from the top of the document.
    const opener = document.activeElement;
    box.current?.focus({ preventScroll: true });
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected)
        opener.focus({ preventScroll: true });
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className={styles.scrim} onMouseDown={onClose}>
      <div
        ref={box}
        className={[styles.dialog, className].filter(Boolean).join(' ')}
        role="dialog"
        aria-label={label}
        data-testid={testId}
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
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
