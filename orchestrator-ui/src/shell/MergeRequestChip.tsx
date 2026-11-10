import type { ChipTone } from '../protocol/chipTone';
import type { MergeRequestStatus, WorktreeMergeRequest } from '../protocol/entities';
import { HueChip } from '../ui/HueChip';
import { GateLozenge } from './GateLozenge';
import styles from './MergeRequestChip.module.css';

const TONES: Record<MergeRequestStatus, ChipTone> = {
  queued: 'busy',
  building: 'busy',
  passed: 'good',
  stale: 'bad',
  failed: 'bad',
  withdrawn: 'quiet',
  merged: 'special',
};

/**
 * A `merge: local` branch's **Merge request**: its id in its status's colour.
 * While its Build runs, the Build's **Gate lozenge** follows it, though the
 * gate job runs in a Build worktree that has no node of its own.
 */
export function MergeRequestChip({
  mr,
  interactive = true,
  className,
}: {
  mr?: WorktreeMergeRequest | null;
  /** Set false inside a link or a button: the lozenge then draws as text. */
  interactive?: boolean;
  className?: string;
}) {
  if (!mr) return null;
  const label = `Merge request ${mr.id}, ${mr.status}`;
  return (
    <span className={[styles.mr, className].filter(Boolean).join(' ')} data-state={mr.status}>
      <HueChip word={mr.status} tone={TONES[mr.status] ?? 'neutral'} label={label}>
        {mr.id}
      </HueChip>
      {mr.status === 'building' && mr.gate && (
        <GateLozenge gate={mr.gate} interactive={interactive} />
      )}
    </span>
  );
}
