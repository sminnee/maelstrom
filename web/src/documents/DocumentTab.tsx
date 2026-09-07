import { useEffect, useRef } from 'react';
import type { Comment } from '../protocol/documents';
import {
  useAddComment,
  useApproveDocument,
  useDocument,
  useRequestChanges,
  useResolveComment,
} from '../api/documents';
import { ApiError } from '../api/http';
import { useWorld } from '../api/useWorld';
import { DecisionCard } from '../decisions/DecisionCard';
import { Markdown } from '../markdown/Markdown';
import { phaseForCommand, phaseLabel } from '../protocol/phase';
import { describeDocumentStatus } from '../selectors/status';
import { sessionTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { useLayoutMode } from '../layout/useLayoutMode';
import { CommentMargin } from './comments/CommentMargin';
import { applyHighlights } from './comments/highlights';
import { useSelectionComment } from './comments/useSelectionComment';
import { AppButton } from '../ui/AppButton';
import { ReviewActions } from './ReviewActions';
import styles from './DocumentTab.module.css';

/**
 * The server serves no comments yet, so the margin holds none: the composer
 * stays, and its button says the server does not do that yet.
 */
const NO_COMMENTS: Comment[] = [];

/** A rendered document, its comment margin, its review actions, and any question its agent asks. */
export function DocumentTab({ documentId }: { documentId: string }) {
  const addComment = useAddComment();
  const resolveComment = useResolveComment();
  const approveDocument = useApproveDocument();
  const requestChanges = useRequestChanges();
  const { world } = useWorld();
  const document = useDocument(documentId);
  const doc = document.data;
  const task = doc ? world.tasks[doc.taskId] : undefined;
  const agent = doc ? world.agents[doc.agentId] : undefined;
  const body = useRef<HTMLDivElement>(null);
  const narrow = useLayoutMode() === 'narrow';
  const { selection, pending, startComment, clear } = useSelectionComment(
    body,
    doc?.markdown ?? '',
  );

  const pendingAnchor = pending?.anchor ?? null;

  useEffect(() => {
    if (!body.current) return;
    return applyHighlights(body.current, NO_COMMENTS, pendingAnchor);
    // Re-run when the pending anchor or the rendered text changes.
  }, [pendingAnchor, doc?.markdown]);

  // Narrowed, so the dock's `DecisionCard` branch keeps a defined agent.
  const waiting = agent !== undefined && agent.pendingRequestIds.length > 0 ? agent : null;
  const phase = task ? phaseForCommand(task.command) : null;
  const created = approveDocument.data?.taskIds;

  if (!doc) {
    const gone = document.error instanceof ApiError && document.error.code === 'unknown_id';
    return (
      <div className={styles.empty} role={document.isError ? 'alert' : undefined}>
        {gone ? (
          `Document ${documentId} is gone.`
        ) : document.isError ? (
          <>
            Could not load the document: {document.error.message}{' '}
            <AppButton onClick={() => document.refetch()}>Retry</AppButton>
          </>
        ) : (
          'Loading…'
        )}
      </div>
    );
  }

  return (
    <div className={styles.document} data-phase={phase ?? undefined} data-testid="document-tab">
      <header className={styles.header}>
        <div className={styles.line}>
          <span className={styles.task}>{doc.taskId}</span>
          {phase && <span className={styles.phase}>{phaseLabel(phase)}</span>}
          <span className={styles.title}>{doc.title}</span>
          <span className={styles.version}>v{doc.version}</span>
          <span className={styles.status} data-status={doc.status}>
            {describeDocumentStatus(doc.status)}
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
      <div className={styles.split}>
        <div className={styles.body} ref={body} data-testid="document-body">
          <Markdown source={doc.markdown} className={styles.prose} />
        </div>
        {/* The margin is a 220px column beside the prose, which on a phone
            would leave the document unreadable. It draws no comment today —
            the server serves none — so hiding it loses nothing. */}
        {!narrow && (
          <CommentMargin
            comments={NO_COMMENTS}
            selection={selection}
            pending={pending}
            onStart={startComment}
            onCancel={clear}
            onAdd={async (anchor, text) => {
              await addComment.mutateAsync({
                documentId,
                version: doc.version,
                anchor,
                body: text,
              });
              clear();
            }}
            onResolve={(commentId) => resolveComment.mutateAsync({ documentId, commentId })}
          />
        )}
      </div>
      {/* One dock, below the document, whoever is waiting. An agent's own wait
          answers the agent, never the document — see `web/DESIGN.md`, "Review
          Dock", and `orchestrator-server.md`, "Commands". */}
      <div className={styles.dock} data-waiting={waiting || undefined} data-testid="review-dock">
        {waiting ? (
          <DecisionCard agent={waiting} variant="dock" inDocumentId={documentId} />
        ) : (
          <ReviewActions
            doc={doc}
            unresolved={0}
            onApprove={() => approveDocument.mutateAsync({ documentId, version: doc.version })}
            onRequestChanges={(summary) =>
              requestChanges.mutateAsync({ documentId, version: doc.version, summary })
            }
          />
        )}
      </div>
      {/* Approving a task set writes to the notebook, so say what it wrote.
          An approve that reports nothing reads as one that did nothing. The
          tasks are not launched: approving a plan and starting work are two
          decisions, and the task list already offers Launch. */}
      {!!created?.length && (
        <div className={styles.created} data-testid="created-tasks">
          Created {created.length === 1 ? '1 task' : `${created.length} tasks`}:{' '}
          {created.join(', ')}
        </div>
      )}
    </div>
  );
}
