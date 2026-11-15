import type { ReactNode } from 'react';
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
  const choices = useHostChoices();
  const agents = useAgents();
  const launch = useLaunch();
  if (!task) return [];
  const lastHost = newestAgent(agents.data?.agents ?? [], task.id)?.host ?? '';
  return hostOptions(
    newSession ? NEW_SESSION : LAUNCH,
    choices,
    (host) => launch.mutateAsync({ taskId: task.id, host, ...(newSession ? { newSession } : {}) }),
    lastHost,
  );
}

/** The hosts work can start on: this server's name, and each online **Remote host**. */
export interface HostChoices {
  ownName: string;
  online: RemoteHostState[];
}

/** The host choices of a launch, shared by a task card's Launch and the New work dialog's Start. */
export function useHostChoices(): HostChoices {
  const host = useHost().data?.host;
  return {
    ownName: host?.name ?? '',
    online: (host?.remoteHosts ?? []).filter((h) => h.online),
  };
}

/** The words of one kind of launch: a first launch, a **New session**, or a Start. */
export interface LaunchWords {
  verb: string;
  processing: string;
  icon: ReactNode;
}

const LAUNCH: LaunchWords = { verb: 'Launch', processing: 'Launching', icon: actionIcon('launch') };
const NEW_SESSION: LaunchWords = {
  verb: 'New session',
  processing: 'Starting',
  icon: actionIcon('launch'),
};
/** The words of the New work dialog's Start. */
export const START: LaunchWords = {
  verb: 'Start',
  processing: 'Starting',
  icon: actionIcon('start'),
};

/** The task's agent that started last. ISO 8601 strings sort as times. */
function newestAgent(agents: Agent[], taskId: TaskId): Agent | undefined {
  let newest: Agent | undefined;
  for (const agent of agents)
    if (agent.taskId === taskId && (!newest || agent.startedAt > newest.startedAt)) newest = agent;
  return newest;
}

/**
 * One option per host: here first, then each online **Remote host**. The
 * default is `sticky` when it is online, else here. `run` gets `undefined`
 * for here.
 */
export function hostOptions(
  words: LaunchWords,
  { ownName, online }: HostChoices,
  run: (host: string | undefined) => Promise<unknown>,
  sticky = '',
): SplitOption[] {
  const chosen = online.some((h) => h.name === sticky) ? sticky : '';
  const option = (host: string, label: string): SplitOption => ({
    label,
    buttonLabel: words.verb,
    icon: words.icon,
    processing: words.processing,
    isDefault: host === chosen,
    run: () => run(host || undefined),
  });
  const here = ownName ? `${words.verb} on ${ownName}` : `${words.verb} here`;
  return [option('', here), ...online.map((h) => option(h.name, `${words.verb} on ${h.name}`))];
}
