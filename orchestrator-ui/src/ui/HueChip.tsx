import type { ComponentType, ReactNode } from 'react';
import type { ChipTone } from '../protocol/chipTone';
import styles from './HueChip.module.css';

export interface HueChipProps {
  /** Where the chip goes. Always external: it opens in a new tab. */
  href?: string;
  /** A 12x12 inline icon, drawn as in `shell/ExternalLinkIcon.tsx`. */
  icon?: ComponentType<{ className?: string }>;
  /** A second mark before the icon, for the service the chip belongs to. */
  brand?: ComponentType<{ className?: string }>;
  /** The word the chip opens to show. One word: it holds one line. */
  word: string;
  tone: ChipTone;
  /**
   * The chip's name, which does not change as the chip opens. Defaults to
   * `word`, which is right when the word says the whole thing.
   */
  label?: string;
  /** What sits between the icon and the word at rest, such as `#118`. */
  children?: ReactNode;
  /** `small` for a dense meta line, `large` for a card's footer. */
  size?: 'small' | 'large';
  className?: string;
}

/**
 * A coloured pill that opens to name itself when you point at it.
 *
 * A dense line has room for a colour but not for a word. The chip keeps the
 * colour on the line and holds the word until a reader asks for it, so a meta
 * row carries the same reading as the card without the width. Hover and
 * keyboard focus open it alike, and the name never changes as it opens — the
 * word is the whole point, so it cannot be mouse-only.
 */
export function HueChip({
  href,
  icon: Icon,
  brand: Brand,
  word,
  tone,
  label,
  children,
  size = 'small',
  className,
}: HueChipProps) {
  const body = (
    <>
      <span className={styles.rest}>
        {Brand && <Brand className={styles.brand} />}
        {Icon && <Icon className={styles.icon} />}
        {children != null && <span className={styles.number}>{children}</span>}
      </span>
      {/* The wrapper is what the width animates, and what clips. A span with
          `overflow: hidden` of its own would cut the word mid-open. */}
      <span className={styles.wordCell} aria-hidden="true">
        <span className={styles.word}>{word}</span>
      </span>
    </>
  );
  // `||`, not `??`: a word the wire made unreadable is empty, not absent, and
  // an empty string would ship a link with no name at all.
  const name = label || word || undefined;
  const shared = {
    className: [styles.chip, className].filter(Boolean).join(' '),
    'data-tone': tone,
    'data-size': size,
    'aria-label': name,
    // The small chip hides its word, so a pointer has only the colour without
    // this. The large chip opens on hover and says it twice over; harmless.
    title: name,
  };
  if (!href)
    return (
      // A reading with nowhere to go draws as text rather than an anchor that
      // does nothing. It still takes focus at the size that opens, because
      // focus is the only way to read the word without a pointer.
      <span {...shared} tabIndex={size === 'large' ? 0 : undefined}>
        {body}
      </span>
    );
  return (
    <a
      {...shared}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      // The node behind it is itself a click target, and opening the chip must
      // not also expand the node.
      onClick={(e) => e.stopPropagation()}
    >
      {body}
    </a>
  );
}
