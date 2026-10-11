import { consoleReading, isUnseen } from '../selectors/operations';
import { useAppStore } from '../store/store';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { useOperationList } from './useConsole';
import styles from './ConsoleControl.module.css';

/** The id the console drawer and screen carry, which the control names. */
export const CONSOLE_ID = 'console';
/** The control's own id: focus falls back to it when the console closes. */
export const CONSOLE_CONTROL_ID = 'console-control';

/**
 * The top bar's way into the **Console**: a terminal icon and a reading of what
 * runs and what is **Unseen**, or `Console` when nothing is. A click
 * opens or closes the console, so Ctrl-` is never the only way in.
 */
export function ConsoleControl() {
  const ops = useOperationList();
  const open = useAppStore((s) => s.ui.console.open);
  const openConsole = useAppStore((s) => s.openConsole);
  const closeConsole = useAppStore((s) => s.closeConsole);
  const reading = consoleReading(ops);
  return (
    <AppButton
      variant="quiet"
      className={styles.control}
      icon={actionIcon('console')}
      id={CONSOLE_CONTROL_ID}
      data-testid="console-control"
      data-fault={ops.some(isUnseen) || undefined}
      aria-expanded={open}
      aria-controls={open ? CONSOLE_ID : undefined}
      title="Console (Ctrl-`)"
      onClick={() => (open ? closeConsole() : openConsole())}
    >
      {reading || 'Console'}
    </AppButton>
  );
}
