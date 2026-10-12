import type { PrReading } from '../selectors/cardPr';
import { describePrState, prTone } from '../selectors/status';
import { GitHubIcon } from './GitHubIcon';
import { HueChip } from '../ui/HueChip';
import styles from './PrChip.module.css';

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
  if (pr.withdrawn) return <WithdrawnPr pr={pr} link={link} className={className} />;
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

/**
 * A **Withdrawn PR**: the GitHub mark and `#118`, grey and struck through, with
 * no pill. The agent is working again, so the PR asks for no review and its
 * state is not drawn. It still opens the PR.
 */
function WithdrawnPr({
  pr,
  link,
  className,
}: {
  pr: PrReading;
  link: boolean;
  className?: string;
}) {
  const label = `PR #${pr.number}, withdrawn`;
  const shared = {
    className: [styles.withdrawn, 'nowrap', className].filter(Boolean).join(' '),
    'aria-label': label,
    title: label,
  };
  const body = (
    <>
      <GitHubIcon className={styles.brand} />
      <span className={styles.number}>#{pr.number}</span>
    </>
  );
  if (!(link && pr.url)) return <span {...shared}>{body}</span>;
  return (
    <a
      {...shared}
      href={pr.url}
      target="_blank"
      rel="noopener noreferrer"
      // As `HueChip`'s: opening the PR must not also expand the node.
      onClick={(e) => e.stopPropagation()}
    >
      {body}
    </a>
  );
}
