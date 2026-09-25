import { useAddToDesk, useRemoveFromDesk } from '../api/desk';
import { deskIdForTask } from '../protocol/deskId';
import type { TaskId } from '../protocol/ids';
import { AppButton } from '../ui/AppButton';

/** Puts a task on the desk, or takes it off. */
export function DeskToggle({
  taskId,
  onDesk,
  variant,
}: {
  taskId: TaskId;
  onDesk: boolean;
  variant?: 'plain' | 'quiet';
}) {
  const addToDesk = useAddToDesk();
  const removeFromDesk = useRemoveFromDesk();
  return (
    <AppButton
      variant={variant}
      onClick={() =>
        (onDesk ? removeFromDesk : addToDesk).mutateAsync({ id: deskIdForTask(taskId) })
      }
    >
      {onDesk ? 'Remove from desk' : 'Add to desk'}
    </AppButton>
  );
}
