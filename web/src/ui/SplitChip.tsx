import type { ReactNode } from 'react';
import type { ChipTone } from '../protocol/chipTone';
import styles from './SplitChip.module.css';

export interface SplitChipProps {
  /** The static half: what is being measured, such as `5h` or `agents`. */
  label: string;
  /** The live half: the reading itself, such as `7%` or `3/5`. */
  children: ReactNode;
  /** Colours the value alone. Defaults to `neutral`; `stale` overrides it. */
  tone?: ChipTone;
  /** The whole sentence, which is both the hover text and the spoken name. */
  title: string;
  /** The reading is too old to stand behind, so both halves go quiet. */
  stale?: boolean;
}

/**
 * A pill in two halves: what is measured, then what it reads.
 *
 * Unlike `HueChip`, which holds its word until a pointer asks, both halves are
 * readable at rest. That is the point — a reading nobody hovers is a reading
 * nobody has, and the top bar exists to be glanced at rather than explored.
 *
 * The divider is what makes it read as two parts. Without it the label and the
 * value run together as one string, and the eye has to parse where one ends.
 */
export function SplitChip({ label, children, tone = 'neutral', title, stale }: SplitChipProps) {
  return (
    <span
      className={styles.chip}
      // A stale reading gives up its tone with its authority, here rather than
      // at each call site: a caller cannot pair `stale` with an alarming tone
      // and get a chip that shouts about a number it cannot vouch for.
      data-tone={stale ? 'quiet' : tone}
      data-stale={stale || undefined}
      // `img` because the chip is one composite reading rather than two texts.
      // A name on a bare span is a name on an element with the `generic` role,
      // which a screen reader discards -- and the halves below are hidden, so
      // the chip would then announce nothing at all.
      role="img"
      aria-label={title}
      title={title}
    >
      {/* The label is said by `aria-label` in full, so the visible halves are
          decoration to a screen reader and would otherwise be read twice. */}
      <span className={styles.label} aria-hidden="true">
        {label}
      </span>
      <span className={styles.value} aria-hidden="true">
        {children}
      </span>
    </span>
  );
}
