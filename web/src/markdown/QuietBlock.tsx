import { useId, useRef, useState } from 'react';
import { AppButton } from '../ui/AppButton';
import { useClamped } from '../ui/useClamped';
import styles from './Markdown.module.css';

/**
 * Working detail, clamped to two lines until asked for.
 *
 * A `<div onClick>` is not keyboard-operable, and a `<button>` wrapper is
 * invalid: a quiet block can carry a link, and a button containing a link is
 * a keyboard trap. So this offers two paths to the same state — a real
 * `AppButton` for the keyboard, and a click on the body for the mouse. The
 * body's click handler steps aside for anything already interactive.
 */
export function QuietBlock({ children }: { children: React.ReactNode }) {
  const [expanded, setExpanded] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const bodyId = useId();
  const clamped = useClamped(body, [children, expanded]);

  const onBodyClick = (e: React.MouseEvent) => {
    if (expanded) return;
    if ((e.target as HTMLElement).closest('a, button, input, [role="button"]')) return;
    setExpanded(true);
  };

  return (
    <div className={styles.quiet} data-testid="quiet">
      <div
        className={styles.quietBody}
        ref={body}
        id={bodyId}
        data-expanded={expanded || undefined}
        onClick={onBodyClick}
      >
        {children}
      </div>
      {(clamped || expanded) && (
        <AppButton
          variant="quiet"
          className={styles.quietMore}
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => setExpanded((was) => !was)}
        >
          {expanded ? 'Show less' : 'Show more'}
        </AppButton>
      )}
    </div>
  );
}
