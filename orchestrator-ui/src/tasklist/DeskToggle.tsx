import { usePutOnDesk, useTakeOffDesk } from '../api/desk';
import { deskIdForTask } from '../protocol/deskId';
import { DeskStateIcon } from '../shell/DeskStateIcon';
import { OffDeskIcon } from '../shell/OffDeskIcon';
import { OnDeskIcon } from '../shell/OnDeskIcon';
import type { TaskId } from '../protocol/ids';
import { AppButton } from '../ui/AppButton';
import styles from './DeskToggle.module.css';

/** Shows if a task is on the desk; a click flips it. Hover shows the act a click takes. */
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
      icon={
        <>
          <DeskStateIcon className={styles.state} onDesk={onDesk} />
          {onDesk ? <OffDeskIcon className={styles.act} /> : <OnDeskIcon className={styles.act} />}
        </>
      }
      onClick={() => (onDesk ? takeOffDesk : putOnDesk).mutateAsync({ id: deskIdForTask(taskId) })}
    >
      On desk
    </AppButton>
  );
}
