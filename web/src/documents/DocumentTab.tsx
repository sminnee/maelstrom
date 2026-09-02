import { Markdown } from '../markdown/Markdown';
import { useAppStore } from '../store/store';
import styles from './DocumentTab.module.css';

/** A rendered document with its version and status. Review lands here in the next slice. */
export function DocumentTab({ documentId }: { documentId: string }) {
  const doc = useAppStore((s) => s.world.documents[documentId]);
  const task = useAppStore((s) => (doc ? s.world.tasks[doc.taskId] : undefined));
  if (!doc) return <div className={styles.empty}>Document {documentId} is gone.</div>;
  return (
    <div className={styles.document} data-phase={task?.phase} data-testid="document-tab">
      <header className={styles.header}>
        <div className={styles.line}>
          <span className={styles.task}>{doc.taskId}</span>
          {task && <span className={styles.phase}>{task.phase}</span>}
          <span className={styles.title}>{doc.title}</span>
          <span className={styles.version}>v{doc.version}</span>
          <span className={styles.status} data-status={doc.status}>
            {doc.status}
          </span>
        </div>
        {task && <div className={styles.taskTitle}>{task.title}</div>}
      </header>
      <div className={styles.body}>
        <Markdown source={doc.markdown} />
      </div>
    </div>
  );
}
