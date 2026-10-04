import { useEnvWorktree } from '../api/worktrees';
import type { Worktree } from '../protocol/entities';
import { actionIcon } from '../ui/actionIcons';
import { SplitButton, type SplitOption } from '../ui/SplitButton';

type EnvAction = 'start' | 'stop' | 'restart';

/**
 * One control for a worktree's environment, reading its env state.
 *
 * The first option is what the state asks for: Stop when it runs, Start when
 * it is stopped or partial, with Stop beside a partial Start. Restart follows,
 * as the common repair. Each optional service then gets its own start or stop.
 * A stopped environment with no optional service draws a plain button.
 */
export function EnvControl({ worktree }: { worktree: Worktree }) {
  const env = useEnvWorktree();
  const act = (action: EnvAction, service?: string) =>
    env.mutateAsync({ worktreeId: worktree.id, action, service });

  const start: SplitOption = {
    label: 'Start env',
    icon: actionIcon('envStart'),
    processing: 'Starting…',
    run: () => act('start'),
  };
  const stop: SplitOption = {
    label: 'Stop env',
    icon: actionIcon('envStop'),
    processing: 'Stopping…',
    run: () => act('stop'),
  };
  const restart: SplitOption = {
    label: 'Restart env',
    icon: actionIcon('envRestart'),
    processing: 'Restarting…',
    run: () => act('restart'),
  };
  const state = worktree.env?.state ?? 'stopped';
  const whole =
    state === 'running' ? [stop, restart] : state === 'partial' ? [start, stop, restart] : [start];
  const optional = (worktree.env?.services ?? [])
    .filter((s) => s.optional)
    .map((s): SplitOption =>
      s.running
        ? {
            label: `Stop ${s.name}`,
            icon: actionIcon('envStop'),
            processing: `Stopping ${s.name}…`,
            run: () => act('stop', s.name),
          }
        : {
            label: `Start ${s.name}`,
            icon: actionIcon('envStart'),
            processing: `Starting ${s.name}…`,
            run: () => act('start', s.name),
          },
    );

  return <SplitButton variant="quiet" options={[...whole, ...optional]} />;
}
