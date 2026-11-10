import { useAgents } from '../api/agents';
import { useHost } from '../api/host';
import { useLaunch } from '../api/tasks';
import type { Agent, RemoteHostState } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import { actionIcon } from '../ui/actionIcons';
import type { SplitOption } from '../ui/SplitButton';

/**
 * Where a task can launch: this server, then each **Remote host** that is online.
 *
 * The default is the host of the task's newest agent when that host is
 * online, because its worktree and transcript are there. Else it is here. The
 * card's split button and a deck row's swipe both run these, so the two
 * cannot launch in different places.
 */
export function useLaunchOptions(task: { id: TaskId } | undefined): SplitOption[] {
  const { data } = useHost();
  const agents = useAgents();
  const launch = useLaunch();
  if (!task) return [];
  const lastHost = newestAgent(agents.data?.agents ?? [], task.id)?.host ?? '';
  return launchOptions(lastHost, data?.host?.name ?? '', data?.host?.remoteHosts ?? [], (host) =>
    launch.mutateAsync({ taskId: task.id, host }),
  );
}

/** The task's agent that started last. ISO 8601 strings sort as times. */
function newestAgent(agents: Agent[], taskId: TaskId): Agent | undefined {
  let newest: Agent | undefined;
  for (const agent of agents)
    if (agent.taskId === taskId && (!newest || agent.startedAt > newest.startedAt)) newest = agent;
  return newest;
}

function launchOptions(
  lastHost: string,
  ownName: string,
  remoteHosts: RemoteHostState[],
  launch: (host: string | undefined) => Promise<unknown>,
): SplitOption[] {
  const online = remoteHosts.filter((h) => h.online);
  const sticky = online.some((h) => h.name === lastHost) ? lastHost : '';
  const option = (host: string, label: string): SplitOption => ({
    label,
    buttonLabel: 'Launch',
    icon: actionIcon('launch'),
    processing: 'Launching',
    isDefault: host === sticky,
    run: () => launch(host || undefined),
  });
  const here = ownName ? `Launch on ${ownName}` : 'Launch here';
  return [option('', here), ...online.map((h) => option(h.name, `Launch on ${h.name}`))];
}
