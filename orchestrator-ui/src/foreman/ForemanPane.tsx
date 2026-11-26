import { useMemo } from 'react';
import { useStartForeman } from '../api/agents';
import { useOperations } from '../api/operations';
import { useWorld } from '../api/useWorld';
import { foremanOf } from '../selectors/agents';
import { SessionTab } from '../session/SessionTab';
import { AppButton } from '../ui/AppButton';
import styles from './ForemanPane.module.css';

/**
 * The **Foreman pane**: the foreman's conversation and its message box. With no live foreman, one
 * button starts it, or resumes the one that exited, so its conversation goes on.
 */
export function ForemanPane() {
  const { world, status } = useWorld();
  const foreman = useMemo(() => foremanOf(world.agents), [world.agents]);
  const start = useStartForeman();
  const operations = useOperations().data?.operations;
  const live = foreman !== undefined && foreman.state !== 'exited';
  // The answer comes before the start ends: the button waits for the operation too.
  const operationId =
    start.data && 'operationId' in start.data ? start.data.operationId : undefined;
  const starting =
    start.isPending ||
    (operationId !== undefined &&
      operations?.find((o) => o.id === operationId)?.state === 'running');
  return (
    <div className={styles.pane} data-testid="foreman-pane">
      {!live && (
        <div className={styles.start}>
          <p className={styles.words}>
            {foreman
              ? 'The foreman has stopped. Resume it to go on with the conversation.'
              : 'The foreman manages your work: ask it about tasks, agents and comms.'}
          </p>
          <AppButton
            variant="primary"
            disabled={status !== 'ready' || starting}
            onClick={() => start.mutate()}
          >
            {foreman ? 'Resume the foreman' : 'Start the foreman'}
          </AppButton>
          {start.error && (
            <p className={styles.error} role="alert">
              {start.error.message}
            </p>
          )}
        </div>
      )}
      {foreman && (
        <div className={styles.session}>
          <SessionTab key={foreman.id} agentId={foreman.id} />
        </div>
      )}
    </div>
  );
}
