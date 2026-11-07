import { useHost } from '../api/host';
import { useLaunch } from '../api/tasks';
import type { RemoteHostState } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import { actionIcon } from '../ui/actionIcons';
import type { SplitOption } from '../ui/SplitButton';

/**
 * Where a task can launch: here, then each **Remote host** that is online.
 *
 * The default is the host of the task's last session when that host is
 * online, because its worktree and transcript are there. Else it is here. The
 * card's split button and a deck row's swipe both run these, so the two
 * cannot launch in different places.
 */
export function useLaunchOptions(
  task: { id: TaskId; lastHost: string } | undefined,
): SplitOption[] {
  const { data } = useHost();
  const launch = useLaunch();
  if (!task) return [];
  return launchOptions(task, data?.host?.remoteHosts ?? [], (host) =>
    launch.mutateAsync({ taskId: task.id, host }),
  );
}

function launchOptions(
  task: { lastHost: string },
  remoteHosts: RemoteHostState[],
  launch: (host: string | undefined) => Promise<unknown>,
): SplitOption[] {
  const online = remoteHosts.filter((h) => h.online);
  const sticky = online.some((h) => h.name === task.lastHost) ? task.lastHost : '';
  const option = (host: string, label: string): SplitOption => ({
    label,
    buttonLabel: online.length ? label : 'Launch',
    icon: actionIcon('launch'),
    processing: 'Launching',
    isDefault: host === sticky,
    run: () => launch(host || undefined),
  });
  return [option('', 'Launch here'), ...online.map((h) => option(h.name, `Launch on ${h.name}`))];
}
