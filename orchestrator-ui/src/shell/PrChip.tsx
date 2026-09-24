import type { Worktree } from '../protocol/entities';
import { describePrState, prTone } from '../selectors/status';
import { GitHubIcon } from './GitHubIcon';
import { HueChip } from '../ui/HueChip';

/**
 * `#118` in the colour GitHub gives the same fact, opening to say it in words.
 *
 * The one place a pull request is drawn, on a collapsed node, a deck row and
 * the card's footer alike, so the same PR reads the same everywhere. It is the
 * PR-shaped adapter over `HueChip`: it decides which reading a state is and
 * which mark it draws, and the chip knows none of it.
 */
export function PrChip({
  worktree,
  size,
  link = true,
  className,
}: {
  worktree?: Worktree;
  size?: 'small' | 'large';
  /**
   * Set false where the chip sits inside a button. An anchor may not nest in
   * one: the HTML is invalid and focus behaviour is undefined. The reading
   * survives — only the link goes, and the row's own click still opens it.
   */
  link?: boolean;
  className?: string;
}) {
  if (!worktree?.prNumber) return null;
  const words = describePrState(worktree.prState, worktree.prDraft);
  return (
    <HueChip
      href={(link && worktree.prUrl) || undefined}
      brand={GitHubIcon}
      word={words}
      tone={prTone(worktree.prState, worktree.prDraft)}
      label={words ? `PR #${worktree.prNumber}, ${words}` : `PR #${worktree.prNumber}`}
      size={size}
      className={className}
    >
      #{worktree.prNumber}
    </HueChip>
  );
}
