import type { PrReading } from '../selectors/cardPr';
import { describePrState, prTone } from '../selectors/status';
import { GitHubIcon } from './GitHubIcon';
import { HueChip } from '../ui/HueChip';

/**
 * `#118` in the colour GitHub gives the same fact, opening to say it in words.
 *
 * The one place a pull request is drawn, on a collapsed node, a deck row and
 * the card's footer alike, so the same PR reads the same everywhere. It is the
 * PR-shaped adapter over `HueChip`: it decides which reading a state is and
 * which mark it draws, and the chip knows none of it. A reading with no state
 * draws the number and link alone.
 */
export function PrChip({
  pr,
  size,
  link = true,
  className,
}: {
  pr?: PrReading;
  size?: 'small' | 'large';
  /**
   * Set false where the chip sits inside a button. An anchor may not nest in
   * one: the HTML is invalid and focus behaviour is undefined. The reading
   * survives — only the link goes, and the row's own click still opens it.
   */
  link?: boolean;
  className?: string;
}) {
  if (!pr?.number) return null;
  const words = describePrState(pr.state, pr.draft);
  return (
    <HueChip
      href={(link && pr.url) || undefined}
      brand={GitHubIcon}
      word={words}
      tone={prTone(pr.state, pr.draft)}
      label={words ? `PR #${pr.number}, ${words}` : `PR #${pr.number}`}
      size={size}
      className={className}
    >
      #{pr.number}
    </HueChip>
  );
}
