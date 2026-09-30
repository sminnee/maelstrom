import type { Worktree } from '../protocol/entities';
import { changesTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { PrChip } from '../shell/PrChip';
import { CmuxControl } from './CmuxControl';
import { DevEnvLinks } from './DevEnvLinks';
import { EnvControl } from './EnvControl';
import { SyncControl } from './SyncControl';

/**
 * A worktree's controls, in two pieces so a surface can lay them out on two
 * rows: the links out of it, and the commands on it. The expanded card and
 * the panel's worktree bar both draw these, so the two cannot drift.
 */

/**
 * The changes, the PR, the dev env and the cmux pane. `pr` is the worktree
 * whose PR to show, `null` for none, and defaults to `worktree`; a card passes
 * `cardPr`'s pick.
 */
export function WorktreeLinks({
  worktree,
  pr = worktree,
}: {
  worktree: Worktree | undefined;
  pr?: Worktree | null;
}) {
  return (
    <>
      {worktree && !worktree.isClosed && (
        <PanelLink tab={changesTab(worktree.id)}>Changes</PanelLink>
      )}
      <PrChip worktree={pr ?? undefined} size="large" />
      <DevEnvLinks worktree={worktree} />
      <CmuxControl worktree={worktree} />
    </>
  );
}

/** Sync and env. A closed worktree has nothing to sync or start. */
export function WorktreeCommands({ worktree }: { worktree: Worktree | undefined }) {
  if (!worktree || worktree.isClosed) return null;
  return (
    <>
      <SyncControl worktree={worktree} />
      <EnvControl worktree={worktree} />
    </>
  );
}
