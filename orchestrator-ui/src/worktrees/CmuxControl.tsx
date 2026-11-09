import { useClient } from '../api/client';
import { useCreateWorktreeTerminal } from '../api/worktrees';
import type { Worktree } from '../protocol/entities';
import { ownerOf } from '../protocol/ids';
import { ExternalLink } from '../shell/ExternalLink';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import styles from '../shell/link.module.css';

/**
 * The worktree's terminal in the cmux this browser can see.
 *
 * When the browser is on the host that owns the worktree: with a terminal,
 * a link that cmux opens on its pane; without one, a button that makes it and
 * then follows its link. When the browser is on another host of this server:
 * always the button, which opens an SSH shell in that host's cmux. The row's
 * `shellUrl` names a pane on the owner, so it is not used. When the browser
 * is on no host the server knows: no control.
 */
export function CmuxControl({ worktree }: { worktree: Worktree | undefined }) {
  const create = useCreateWorktreeTerminal();
  const { data: client } = useClient();
  if (!worktree || worktree.isClosed || !client) return null;
  if (!client.local && !client.host) return null;
  const ssh = ownerOf(worktree.id) !== client.host;

  if (!ssh && worktree.shellUrl) {
    return (
      <ExternalLink href={worktree.shellUrl} icon="terminal" newTab={false}>
        cmux
      </ExternalLink>
    );
  }
  return (
    <AppButton
      variant="link"
      processingChildren="cmux"
      title={ssh ? `SSH to ${worktree.sshTarget}` : undefined}
      onClick={async () => {
        const { shellUrl } = await create.mutateAsync({ worktreeId: worktree.id, ssh });
        window.location.assign(shellUrl);
      }}
    >
      cmux
      {actionIcon('new', styles.icon)}
    </AppButton>
  );
}
