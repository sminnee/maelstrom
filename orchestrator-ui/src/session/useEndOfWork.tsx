import { useStop } from '../api/agents';
import { useDismiss, useTakeOffDesk, type DismissChain } from '../api/desk';
import { useWorld } from '../api/useWorld';
import type { Agent, Worktree } from '../protocol/entities';
import type { DeskId, TaskId } from '../protocol/ids';
import { isLive, workDeskId, worktreeHolders } from '../selectors/graph';
import { canClose } from '../selectors/worktrees';
import type { WorldView } from '../selectors/world';
import { OffDeskIcon } from '../shell/OffDeskIcon';
import { actionIcon } from '../ui/actionIcons';
import type { SplitOption } from '../ui/SplitButton';
import { removeConfirm, shelveConfirm, trashConfirm } from '../worktrees/closeConfirms';

export interface EndOfWorkProps {
  agent: Agent | undefined;
  taskId?: TaskId;
  where: Worktree | undefined;
  onTakenOffDesk: () => void;
}

/**
 * The end-of-work options for a node. The one with `isDefault` is Dismiss. The
 * card's split button and a deck row's swipe both run these, so the two cannot
 * drift on what Dismiss does.
 */
export function useEndOfWork(props: EndOfWorkProps): SplitOption[] {
  return useEndOfWorkLater(props)();
}

/**
 * `useEndOfWork`, built only when called. A deck row needs the options at the
 * moment of a swipe, not on every render of every row.
 */
export function useEndOfWorkLater({
  agent,
  taskId,
  where,
  onTakenOffDesk,
}: EndOfWorkProps): () => SplitOption[] {
  const { world } = useWorld();
  const stop = useStop();
  const takeOffDesk = useTakeNodeOffDesk({ agent, taskId, onTakenOffDesk });
  const dismiss = useNodeDismiss({ agent, taskId, onTakenOffDesk });
  return () =>
    endOfWorkOptions({
      live: isLive(agent),
      where,
      others: where ? otherHolders(world, where, workDeskId(agent, taskId)) : NO_HOLDERS,
      // Terminate ends the process; the session tab's Stop only abandons the
      // turn — see CONTEXT.md, "Interrupt".
      stop: () => stop.mutateAsync({ agentId: agent!.id }),
      takeOffDesk,
      // A terminate ends no worktree, so it names none.
      dismiss: (chain) => dismiss(chain, chain === 'terminate' ? undefined : where?.id),
    });
}

/** The **Holders** of a worktree other than the node itself, counted by kind. */
interface OtherHolders {
  /** Holders a live agent runs. */
  running: number;
  /** Free agents on the desk with no live agent. */
  agents: number;
  /** Tasks with no live agent. */
  tasks: number;
}

const NO_HOLDERS: OtherHolders = { running: 0, agents: 0, tasks: 0 };

function otherHolders(world: WorldView, where: Worktree, own: DeskId): OtherHolders {
  const others = { ...NO_HOLDERS };
  for (const [deskId, { kind, running }] of worktreeHolders(world, where)) {
    if (deskId === own) continue;
    if (running) others.running++;
    else if (kind === 'agent') others.agents++;
    else others.tasks++;
  }
  return others;
}

/** Why the close is held, or `undefined` when nothing holds it. */
function heldDetail({ running, agents, tasks }: OtherHolders, nato: string): string | undefined {
  const plural = (n: number, noun: string) => `${n} other ${noun}${n === 1 ? '' : 's'}`;
  if (agents + tasks === 0) {
    return running ? `${plural(running, 'agent')} still running in ${nato}` : undefined;
  }
  const parts = [
    ...(running + agents ? [plural(running + agents, 'agent')] : []),
    ...(tasks ? [plural(tasks, 'task')] : []),
  ];
  const verb = running + agents + tasks === 1 ? 'uses' : 'use';
  return `${parts.join(' and ')} still ${verb} ${nato}`;
}

/**
 * Start a **Dismiss** chain on a node, then run `onTakenOffDesk` at once,
 * before the server has taken the node off the desk. The server runs the whole
 * chain as one operation, so a reload does not lose it partway — see
 * `docs/dev/orchestrator-ui.md`.
 */
export function useNodeDismiss({
  agent,
  taskId,
  onTakenOffDesk,
}: Omit<EndOfWorkProps, 'where'>): (chain: DismissChain, worktreeId?: string) => Promise<void> {
  const dismiss = useDismiss();
  return async (chain, worktreeId) => {
    await dismiss.mutateAsync({ deskId: workDeskId(agent, taskId), chain, worktreeId });
    onTakenOffDesk();
  };
}

/**
 * **Off desk** alone, for a node with nothing left to stop or end: the node's
 * desk entry goes, then `onTakenOffDesk` runs.
 */
export function useTakeNodeOffDesk({
  agent,
  taskId,
  onTakenOffDesk,
}: Omit<EndOfWorkProps, 'where'>): () => Promise<void> {
  const { world } = useWorld();
  const offDesk = useTakeOffDesk();
  return async () => {
    const id = workDeskId(agent, taskId);
    // A live node draws with no desk entry, so there may be none to take.
    if (id in world.desk) await offDesk.mutateAsync({ id });
    onTakenOffDesk();
  };
}

/**
 * The end-of-work control's options. The default is the Dismiss chain — see
 * `CONTEXT.md`, "Dismiss". Each chain but Terminate and Off desk is one
 * `dismiss` operation on the server — see `docs/dev/orchestrator-ui.md`.
 */
function endOfWorkOptions({
  live,
  where,
  others,
  stop,
  takeOffDesk,
  dismiss,
}: {
  live: boolean;
  where: Worktree | undefined;
  /** The **Holders** of `where` other than this node. */
  others: OtherHolders;
  stop: () => Promise<unknown>;
  takeOffDesk: () => Promise<unknown>;
  dismiss: (chain: DismissChain) => Promise<unknown>;
}): SplitOption[] {
  const options: SplitOption[] = live
    ? [
        {
          label: 'Terminate',
          icon: actionIcon('terminate'),
          processing: 'Terminating…',
          run: stop,
        },
        {
          label: '…and take off desk',
          icon: <OffDeskIcon />,
          processing: 'Terminating…',
          run: () => dismiss('terminate'),
        },
      ]
    : [
        {
          label: 'Off desk',
          icon: <OffDeskIcon />,
          processing: 'Taking off desk…',
          run: takeOffDesk,
        },
      ];
  // Dismiss runs the close chain when it can, else the widest chain that keeps the worktree.
  let byDefault = options.at(-1)!;
  if (where && canClose(where)) {
    const detail = heldDetail(others, where.nato);
    const held = { icon: <OffDeskIcon />, disabled: detail !== undefined, detail };
    const closeChain: SplitOption = {
      label: `…and close ${where.nato}`,
      processing: 'Closing…',
      ...held,
      run: () => dismiss('close'),
    };
    if (!closeChain.disabled) byDefault = closeChain;
    options.push(
      closeChain,
      {
        label: '…shelving the branch',
        processing: 'Shelving…',
        ...held,
        confirm: shelveConfirm(where),
        run: () => dismiss('shelve'),
      },
      {
        label: '…or trashing the branch',
        processing: 'Trashing…',
        ...held,
        confirm: trashConfirm(where),
        run: () => dismiss('trash'),
      },
      {
        label: '…or ignoring the branch',
        processing: 'Deleting…',
        ...held,
        confirm: removeConfirm(where),
        run: () => dismiss('delete'),
      },
    );
  }
  return options.map((o) =>
    o === byDefault ? { ...o, isDefault: true, buttonLabel: 'Dismiss' } : o,
  );
}
