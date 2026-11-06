import { useProjectDetail } from '../nav/useOverlays';
import { ProjectDetail } from '../projects/ProjectDetail';

/** The dialogs about projects, which the location opens over any view. */
export function ProjectOverlays() {
  const detail = useProjectDetail();
  return <>{detail.projectId && <ProjectDetail projectId={detail.projectId} />}</>;
}
