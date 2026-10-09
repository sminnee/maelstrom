import { useCallback, useEffect, useRef } from 'react';
import { actionIcon } from './actionIcons';
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
 * the focus and draws the `::backdrop`.
 *
 * `placement="side"` draws it as the narrow layout's side sheet: full height
 * at the right edge, rather than centred.
 */
export function Dialog({
  label,
  onClose,
  testId,
  className,
  placement = 'centre',
  children,
}: {
  /** The dialog's accessible name. */
  label: string;
  /** Escape or a click on the backdrop, or the header's close. */
  onClose: () => void;
  testId?: string;
  /** Added to the box, for a dialog whose content is not text. */
  className?: string;
  placement?: 'centre' | 'side';
  children: React.ReactNode;
}) {
  const box = useRef<HTMLDialogElement>(null);
  // Whether the last press was on the backdrop. A click lands on the nearest
  // element both the press and the release were in, so a drag from a field out
  // to the backdrop clicks the dialog too.
  const backdropPress = useRef(false);

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
      className={[styles.dialog, placement === 'side' && styles.side, className]
        .filter(Boolean)
        .join(' ')}
      aria-label={label}
      data-testid={testId}
      tabIndex={-1}
      // Light dismiss. A descendant, such as a combo box offer drawn past the
      // box's edge, is inside by the DOM tree; the box's own padding is inside
      // by its rect.
      closedby="any"
      // Escape, and a backdrop click under `closedby`, arrive as `cancel`. Taking
      // it here, not on the document, lets a control inside stop Escape first;
      // the combo box does.
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      {...(!hasLightDismiss() && {
        onPointerDown: (e: React.PointerEvent<HTMLDialogElement>) => {
          backdropPress.current = onBackdrop(e);
        },
        onClick: (e: React.MouseEvent<HTMLDialogElement>) => {
          if (backdropPress.current && onBackdrop(e)) onClose();
          backdropPress.current = false;
        },
      })}
    >
      {children}
    </dialog>
  );
}

/** Whether `closedby` works here. Where it does, the fallback would close twice. */
const hasLightDismiss = () => 'closedBy' in HTMLDialogElement.prototype;

/**
 * Whether a pointer event is on the backdrop: on the dialog itself, at a point
 * outside its box. The box's own padding is the dialog too, but inside the box.
 */
function onBackdrop(e: React.MouseEvent<HTMLDialogElement>): boolean {
  if (e.target !== e.currentTarget) return false;
  const box = e.currentTarget.getBoundingClientRect();
  return (
    e.clientX < box.left || e.clientX > box.right || e.clientY < box.top || e.clientY > box.bottom
  );
}

/** The dialog's title row, with the close button. */
export function DialogHeader({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  /** Extra controls beside the title, e.g. a task editor's Prev/Next. */
  children?: React.ReactNode;
}) {
  return (
    <header className={styles.header}>
      <h2 className={styles.heading}>{title}</h2>
      {/* One flex item, so a multi-button child (e.g. Prev/Next) clusters
          beside the close button rather than spreading across the header. */}
      {children && <span className={styles.headerControls}>{children}</span>}
      <button type="button" className={styles.close} aria-label="Close" onClick={onClose}>
        {actionIcon('close')}
      </button>
    </header>
  );
}

/**
 * The fields of a dialog, between `DialogHeader` and `DialogFooter`. Only the
 * body scrolls. See DESIGN.md, "The Still Screen Rule".
 */
export function DialogBody({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={[styles.body, className].filter(Boolean).join(' ')} data-testid="dialog-body">
      {children}
    </div>
  );
}

/**
 * The button row a dialog ends with: `aside` on the left, `children` on the
 * right. A comment box ends with it too. See DESIGN.md § Dialog footers.
 */
export function DialogFooter({
  aside,
  className,
  children,
}: {
  /** Exception actions: `AppButton variant="link"`, with no icon. */
  aside?: React.ReactNode;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <footer className={[styles.footer, className].filter(Boolean).join(' ')}>
      {aside && <div className={styles.aside}>{aside}</div>}
      <div className={styles.main}>{children}</div>
    </footer>
  );
}
