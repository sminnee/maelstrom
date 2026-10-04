import { usePutOnDesk, useTakeOffDesk } from '../api/desk';
import { deskIdForTask } from '../protocol/deskId';
import type { TaskId } from '../protocol/ids';
import { OffDeskIcon } from '../shell/OffDeskIcon';
import { OnDeskIcon } from '../shell/OnDeskIcon';
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
  const putOnDesk = usePutOnDesk();
  const takeOffDesk = useTakeOffDesk();
  return (
    <AppButton
      variant={variant}
      icon={onDesk ? <OffDeskIcon /> : <OnDeskIcon />}
      onClick={() => (onDesk ? takeOffDesk : putOnDesk).mutateAsync({ id: deskIdForTask(taskId) })}
    >
      {onDesk ? 'Off desk' : 'On desk'}
    </AppButton>
  );
}
