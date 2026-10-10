import { useWorld } from '../api/useWorld';
import type { Worktree } from '../protocol/entities';
import { worktreePr, type PrReading } from '../selectors/cardPr';
import { changesTab } from '../selectors/tabs';
import { trackedAgents } from '../selectors/worktrees';
import { PanelLink } from '../shell/PanelLink';
import { PrChip } from '../shell/PrChip';
import { CloseControl } from './CloseControl';
import { CmuxControl } from './CmuxControl';
import { DevEnvLinks } from './DevEnvLinks';
import { EnvControl } from './EnvControl';
import { MergeControl } from './MergeControl';
import { SyncControl } from './SyncControl';

/**
 * A worktree's controls, in two pieces so a surface can lay them out on two
 * rows: the links out of it, and the commands on it. The worktree table, the
 * worktree area and the panel's worktree bar all draw these, so they cannot
 * drift.
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

/**
 * Merge, sync, env and the close control. A closed worktree has nothing to
 * merge, sync or start, so it gets the close control alone. While an agent
 * runs in the worktree, `busyClose` says what the close does: `hold` draws it
 * disabled, `hide` leaves it out — see `docs/dev/orchestrator-ui.md`, "The
 * worktree area".
 */
export function WorktreeCommands({
  worktree,
  busyClose = 'hold',
}: {
  worktree: Worktree | undefined;
  busyClose?: 'hold' | 'hide';
}) {
  const { world } = useWorld();
  if (!worktree) return null;
  if (worktree.isClosed) return <CloseControl worktree={worktree} />;
  const busy = trackedAgents(world, worktree.id).length > 0;
  return (
    <>
      <MergeControl worktree={worktree} />
      <SyncControl worktree={worktree} />
      <EnvControl worktree={worktree} />
      {!(busy && busyClose === 'hide') && <CloseControl worktree={worktree} />}
    </>
  );
}
