import { useState } from 'react';
import { useWorld } from '../api/useWorld';
import { useForceCloseWorktree, useRemoveWorktree, useTrashWorktree } from '../api/worktrees';
import type { WorktreeId } from '../protocol/ids';
import { actionIcon } from '../ui/actionIcons';
import { ConfirmButton } from '../ui/ConfirmButton';
import { removeConfirm, shelveConfirm, trashConfirm } from './closeConfirms';
import styles from './NextChains.module.css';

export type Chain = 'shelve' | 'trash' | 'delete';

/**
 * What a refused close can do next: the closes that skip its checks, each with
 * the confirm the close control asks. `dismiss`, when given, runs the chosen
 * close as a Dismiss chain in its place, so a refused Dismiss still ends as a
 * Dismiss: closed, then off the desk.
 */
export function NextChains({
  worktreeId,
  dismiss,
}: {
  worktreeId: WorktreeId;
  dismiss?: (chain: Chain) => Promise<unknown>;
}) {
  const worktree = useWorld().world.worktrees[worktreeId];
  const shelve = useForceCloseWorktree();
  const trash = useTrashWorktree();
  const remove = useRemoveWorktree();
  const [asking, setAsking] = useState<Chain | null>(null);
  if (!worktree || worktree.isClosed) return null;
  const target = { worktreeId: worktree.id };
  const chains: {
    chain: Chain;
    label: string;
    icon: Parameters<typeof actionIcon>[0];
    confirm: typeof shelveConfirm;
    run: () => Promise<unknown>;
  }[] = [
    {
      chain: 'shelve',
      label: 'Shelve',
      icon: 'archive',
      confirm: shelveConfirm,
      run: () => shelve.mutateAsync(target),
    },
    {
      chain: 'trash',
      label: 'Trash',
      icon: 'delete',
      confirm: trashConfirm,
      run: () => trash.mutateAsync(target),
    },
    {
      chain: 'delete',
      label: 'Delete',
      icon: 'removeWorktree',
      confirm: removeConfirm,
      run: () => remove.mutateAsync(target),
    },
  ];
  return (
    <div className={styles.next} role="group" aria-label="Next">
      {chains.map(({ chain, label, icon, confirm, run }) => {
        const { question, confirm: yes } = confirm(worktree);
        return (
          <ConfirmButton
            key={chain}
            icon={actionIcon(icon)}
            question={question}
            confirm={yes}
            asking={asking === chain}
            onAsk={() => setAsking(chain)}
            onDismiss={() => setAsking(null)}
            onConfirm={() => (dismiss ? dismiss(chain) : run())}
          >
            {label}
          </ConfirmButton>
        );
      })}
    </div>
  );
}
