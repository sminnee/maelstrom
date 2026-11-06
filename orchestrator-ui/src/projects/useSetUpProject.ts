import { useCallback } from 'react';
import { useCreateTask } from '../api/tasks';
import type { ProjectId } from '../protocol/ids';
import { SETUP_COMMAND } from '../selectors/projects';

/**
 * Create and launch the task that runs `/setup-project` on a project. The title and branch are
 * known, so the server does not infer them. The mode is explicit: a typed command with no mode
 * starts in plan mode.
 */
export function useSetUpProject() {
  const create = useCreateTask();
  const { mutateAsync } = create;
  const setUp = useCallback(
    (project: ProjectId) =>
      mutateAsync({
        project,
        title: `Set up ${project}`,
        branch: 'chore/setup-project',
        command: SETUP_COMMAND,
        mode: 'normal',
        launch: true,
      }),
    [mutateAsync],
  );
  return { ...create, setUp };
}
