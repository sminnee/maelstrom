import type { Worktree } from '../protocol/entities';
import { worktreePr, type PrReading } from '../selectors/cardPr';
import { changesTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { PrChip } from '../shell/PrChip';
import { CmuxControl } from './CmuxControl';
import { DevEnvLinks } from './DevEnvLinks';
import { EnvControl } from './EnvControl';
import { MergeControl } from './MergeControl';
import { SyncControl } from './SyncControl';

/**
 * A worktree's controls, in two pieces so a surface can lay them out on two
 * rows: the links out of it, and the commands on it. The worktree area and
 * the panel's worktree bar both draw these, so the two cannot drift.
 */

/**
 * The changes, the PR, the dev env and the cmux pane. `pr` is the PR to show,
 * `null` for none, and defaults to the worktree's own; a card passes
 * `cardPr`'s pick.
 */
export function WorktreeLinks({
  worktree,
  pr = worktreePr(worktree),
  chipClassName,
}: {
  worktree: Worktree | undefined;
  pr?: PrReading | null;
  /** For the PR chip: the narrow detail screen gives it a row. */
  chipClassName?: string;
}) {
  return (
    <>
      {worktree && !worktree.isClosed && (
        <PanelLink tab={changesTab(worktree.id)}>Changes</PanelLink>
      )}
      <PrChip pr={pr ?? undefined} size="large" className={chipClassName} />
      <DevEnvLinks worktree={worktree} />
      <CmuxControl worktree={worktree} />
    </>
  );
}

/** Merge, sync and env. A closed worktree has nothing to merge, sync or start. */
export function WorktreeCommands({ worktree }: { worktree: Worktree | undefined }) {
  if (!worktree || worktree.isClosed) return null;
  return (
    <>
      <MergeControl worktree={worktree} />
      <SyncControl worktree={worktree} />
      <EnvControl worktree={worktree} />
    </>
  );
}
