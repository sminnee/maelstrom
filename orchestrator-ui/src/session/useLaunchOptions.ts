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
export function useLaunchOptions(
  task: { id: TaskId } | undefined,
  { newSession = false }: { newSession?: boolean } = {},
): SplitOption[] {
  const { data } = useHost();
  const agents = useAgents();
  const launch = useLaunch();
  if (!task) return [];
  const lastHost = newestAgent(agents.data?.agents ?? [], task.id)?.host ?? '';
  return launchOptions(
    lastHost,
    data?.host?.name ?? '',
    data?.host?.remoteHosts ?? [],
    newSession ? NEW_SESSION : LAUNCH,
    (host) => launch.mutateAsync({ taskId: task.id, host, ...(newSession ? { newSession } : {}) }),
  );
}

/** The words of one kind of launch: a first launch, or a **New session**. */
interface LaunchWords {
  verb: string;
  processing: string;
}

const LAUNCH: LaunchWords = { verb: 'Launch', processing: 'Launching' };
const NEW_SESSION: LaunchWords = { verb: 'New session', processing: 'Starting' };

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
  words: LaunchWords,
  launch: (host: string | undefined) => Promise<unknown>,
): SplitOption[] {
  const online = remoteHosts.filter((h) => h.online);
  const sticky = online.some((h) => h.name === lastHost) ? lastHost : '';
  const option = (host: string, label: string): SplitOption => ({
    label,
    buttonLabel: words.verb,
    icon: actionIcon('launch'),
    processing: words.processing,
    isDefault: host === sticky,
    run: () => launch(host || undefined),
  });
  const here = ownName ? `${words.verb} on ${ownName}` : `${words.verb} here`;
  return [option('', here), ...online.map((h) => option(h.name, `${words.verb} on ${h.name}`))];
}
