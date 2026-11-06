import { useWorld } from '../api/useWorld';
import { useAddProjectDialog, useProjectDetail, useWelcome } from '../nav/useOverlays';
import { AddProject } from '../projects/AddProject';
import { ProjectDetail } from '../projects/ProjectDetail';
import { WelcomeDialog } from '../welcome/WelcomeDialog';

/**
 * The dialogs about projects, which the location opens over any view: the Welcome dialog, Add
 * project, and the Project dialog. They chain into each other, so one place draws all three.
 */
export function ProjectOverlays() {
  const { world, status } = useWorld();
  const welcome = useWelcome();
  const add = useAddProjectDialog();
  const detail = useProjectDetail();
  // A fresh install greets the user once per page load, unless another dialog has the screen.
  const firstRun =
    status === 'ready' &&
    Object.keys(world.projects).length === 0 &&
    !welcome.dismissed &&
    !add.adding &&
    !detail.projectId;
  return (
    <>
      {(welcome.asked || firstRun) && <WelcomeDialog />}
      {add.adding && <AddProject />}
      {detail.projectId && <ProjectDetail projectId={detail.projectId} />}
    </>
  );
}
