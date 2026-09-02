import { useEffect, useRef } from 'react';
import { DecisionCard } from '../decisions/DecisionCard';
import { Markdown } from '../markdown/Markdown';
import { sessionTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { useAppStore } from '../store/store';
import { useCommand } from '../store/useCommand';
import { CommentMargin } from './comments/CommentMargin';
import { applyHighlights } from './comments/highlights';
import { useSelectionComment } from './comments/useSelectionComment';
import { ReviewActions } from './ReviewActions';
import styles from './DocumentTab.module.css';

/** A rendered document, its comment margin, its review actions, and any question its agent asks. */
export function DocumentTab({ documentId }: { documentId: string }) {
  const { send, error } = useCommand();
  const doc = useAppStore((s) => s.world.documents[documentId]);
  const task = useAppStore((s) => (doc ? s.world.tasks[doc.taskId] : undefined));
  const agent = useAppStore((s) => (doc ? s.world.agents[doc.agentId] : undefined));
  const allComments = useAppStore((s) => s.world.comments);
  const body = useRef<HTMLDivElement>(null);
  const { pending, onMouseUp, clear } = useSelectionComment(body, doc?.markdown ?? '');

  const comments = Object.values(allComments)
    .filter((c) => c.documentId === documentId && c.version === doc?.version)
    .sort((a, b) => a.anchor.start - b.anchor.start || a.createdAt.localeCompare(b.createdAt));
  const unresolved = comments.filter((c) => !c.resolved).length;

  useEffect(() => {
    if (!body.current) return;
    return applyHighlights(body.current, comments);
    // Re-run when the comment set or the rendered text changes.
  }, [comments, doc?.markdown]);

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
        <div className={styles.taskLine}>
          {task && <span className={styles.taskTitle}>{task.title}</span>}
          {agent && (
            <PanelLink tab={sessionTab(agent.id)} className={styles.sessionLink}>
              Session
            </PanelLink>
          )}
        </div>
      </header>
      {/* A plan review is answered by the review bar below, so it is not shown twice. */}
      {!!agent?.pendingRequestId && agent.state !== 'awaiting-plan-review' && (
        <div className={styles.question} data-testid="inline-decision">
          <DecisionCard agent={agent} />
        </div>
      )}
      <div className={styles.split}>
        <div className={styles.body} ref={body} onMouseUp={onMouseUp} data-testid="document-body">
          <Markdown source={doc.markdown} />
        </div>
        <CommentMargin
          comments={comments}
          pending={pending}
          onCancel={clear}
          onAdd={(anchor, text) => {
            void send({
              type: 'comment.add',
              documentId,
              version: doc.version,
              anchor,
              body: text,
            }).then(clear);
          }}
          onResolve={(commentId) => void send({ type: 'comment.resolve', commentId })}
        />
      </div>
      {error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}
      <ReviewActions
        doc={doc}
        unresolved={unresolved}
        onApprove={() => void send({ type: 'document.approve', documentId, version: doc.version })}
        onRequestChanges={(summary) =>
          void send({ type: 'document.requestChanges', documentId, version: doc.version, summary })
        }
      />
    </div>
  );
}
