import type { ToolCallStatus } from '../../protocol/transcript';
import styles from './cards.module.css';

export function BashCard({
  command,
  output,
  status,
}: {
  command: string;
  output?: string;
  status: ToolCallStatus;
}) {
  return (
    <div className={styles.bash}>
      <pre className={styles.command}>$ {command}</pre>
      {output !== undefined && (
        <pre className={styles.output} data-error={status === 'error' || undefined}>
          {output}
        </pre>
      )}
    </div>
  );
}
