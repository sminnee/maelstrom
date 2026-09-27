import { useCreateWorktreeTerminal } from '../api/worktrees';
import type { Worktree } from '../protocol/entities';
import { ExternalLink } from '../shell/ExternalLink';
import { PlusIcon } from '../shell/PlusIcon';
import { TerminalIcon } from '../shell/TerminalIcon';
import { AppButton } from '../ui/AppButton';
import styles from './CmuxControl.module.css';

/**
 * The worktree's terminal in cmux. With one, a link that cmux opens on its
 * pane. Without one, a button that makes it and then follows its link.
 */
export function CmuxControl({ worktree }: { worktree: Worktree | undefined }) {
  const create = useCreateWorktreeTerminal();
  if (!worktree || worktree.isClosed) return null;

  if (worktree.shellUrl) {
    return (
      <ExternalLink href={worktree.shellUrl} icon={TerminalIcon} newTab={false}>
        cmux
      </ExternalLink>
    );
  }
  return (
    <AppButton
      variant="link"
      processingChildren="cmux"
      onClick={async () => {
        const { shellUrl } = await create.mutateAsync({ worktreeId: worktree.id });
        window.location.assign(shellUrl);
      }}
    >
      cmux
      <PlusIcon className={styles.icon} />
    </AppButton>
  );
}
