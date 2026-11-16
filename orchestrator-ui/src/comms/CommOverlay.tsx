import { useCommDetail } from '../nav/useOverlays';
import { CommEditor } from './CommEditor';

/** The comm editor the location's `cdetail` opens, over any view: a comm chip opens it. */
export function CommOverlay() {
  const detail = useCommDetail();
  if (!detail.commId) return null;
  return <CommEditor key={detail.commId} commId={detail.commId} onClose={detail.close} />;
}
