import { usePutOnDesk, useTakeOffDesk } from '../api/desk';
import { deskIdForTask } from '../protocol/deskId';
import { DeskStateIcon } from '../shell/DeskStateIcon';
import type { TaskId } from '../protocol/ids';
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
      icon={<DeskStateIcon onDesk={onDesk} />}
      onClick={() => (onDesk ? takeOffDesk : putOnDesk).mutateAsync({ id: deskIdForTask(taskId) })}
    >
      On desk
    </AppButton>
  );
}
