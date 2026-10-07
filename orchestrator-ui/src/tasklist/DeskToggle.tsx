import { usePutOnDesk, useTakeOffDesk } from '../api/desk';
import { deskIdForTask } from '../protocol/deskId';
import type { TaskId } from '../protocol/ids';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import styles from './DeskToggle.module.css';

/** Shows if a task is on the desk; a click flips it. */
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
      className={styles.toggle}
      aria-pressed={onDesk}
      icon={actionIcon(onDesk ? 'checked' : 'unchecked')}
      onClick={() => (onDesk ? takeOffDesk : putOnDesk).mutateAsync({ id: deskIdForTask(taskId) })}
    >
      On desk
    </AppButton>
  );
}
