import { useCallback, useEffect, useRef, useState } from 'react';
import type { Comment } from '../protocol/documents';
import {
  useAddComment,
  useApproveDocument,
  useArchiveDocument,
  useDocument,
  useRequestChanges,
  useResolveComment,
} from '../api/documents';
import { ApiError } from '../api/http';
import { useWorld } from '../api/useWorld';
import { DecisionCard } from '../decisions/DecisionCard';
import { HEADING_ID_PREFIX, Markdown } from '../markdown/Markdown';
import { phaseForTask, phaseLabel } from '../protocol/phase';
import { groupOf } from '../selectors/documents';
import { agentsByTask } from '../selectors/graph';
import type { DocumentRow } from '../selectors/world';
import { describeDocumentStatus } from '../selectors/status';
import { documentTab, sessionTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { ScreenSheet } from '../shell/ScreenChrome';
import { useScreenChrome } from '../shell/screenChromeContext';
import { useLayoutMode } from '../layout/useLayoutMode';
import { useGo } from '../nav/useNav';
import { CommentMargin } from './comments/CommentMargin';
import { applyHighlights } from './comments/highlights';
import { useSelectionComment } from './comments/useSelectionComment';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { useExpandableClamp } from '../ui/useExpandableClamp';
import { ReferencedBy } from './ReferencedBy';
import { ReviewActions } from './ReviewActions';
import { VersionPicker } from './VersionPicker';
import styles from './DocumentTab.module.css';
import { qualifiedTaskLabel, taskLabelTitle } from '../selectors/taskLabel';

/**
 * The server serves no comments yet, so the margin holds none: the composer
 * stays, and its button says the server does not do that yet.
 */
const NO_COMMENTS: Comment[] = [];

/**
 * A rendered document, its comment margin, its review actions, and any
 * question its agent asks. With `version` it shows that earlier version, read
 * only; with none it shows the latest and follows new ones. With `fragment`
 * it scrolls to that heading.
 */
export function DocumentTab({
  documentId,
  version,
  fragment,
}: {
  documentId: string;
  version?: number;
  fragment?: string;
}) {
  const addComment = useAddComment();
  const resolveComment = useResolveComment();
  const approveDocument = useApproveDocument();
  const requestChanges = useRequestChanges();
  const archiveDocument = useArchiveDocument();
  const go = useGo();
  const { world } = useWorld();
  const document = useDocument(documentId, version);
  const doc = document.data;
  const task = doc ? world.tasks[doc.taskId] : undefined;
  const agent = doc ? world.agents[doc.agentId] : undefined;
  const body = useRef<HTMLDivElement>(null);
  const narrow = useLayoutMode() === 'narrow';
  // The dock stays in the body: it is the terminal act. See DESIGN.md, "The
  // One Strip Rule".
  const chrome = useScreenChrome();
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

  // A fragment no heading of this version has: the note says so, quietly.
  const [missing, setMissing] = useState<string | null>(null);
  const scrollTo = useCallback((target: string) => {
    const heading = body.current?.querySelector(`#${CSS.escape(HEADING_ID_PREFIX + target)}`);
    heading?.scrollIntoView({ block: 'start' });
    setMissing(heading ? null : target);
  }, []);
  useEffect(() => {
    if (fragment) scrollTo(fragment);
    // Again when the shown version, and so the rendered body, changes.
  }, [fragment, doc?.markdown, scrollTo]);

  /** Show `place` in this tab: a version, a fragment, or the latest with neither. */
  const show = (place: { version?: number; fragment?: string }) =>
    go({ panel: documentTab(documentId, place) }, { replace: true });

  // Narrowed, so the dock's `DecisionCard` branch keeps a defined agent.
  const waiting = agent !== undefined && agent.pendingRequestIds.length > 0 ? agent : null;
  const phase = task ? phaseForTask(task, agentsByTask(world).get(task.id)) : null;
  const created = approveDocument.data?.taskIds;
  // The list row carries the group; the detail may be a version behind it.
  const row = world.documents[documentId];
  // A superseded document left its group, so it stands alone.
  const members = row && row.status !== 'superseded' ? groupOf(world, row) : [];

  if (!doc) {
    const gone = document.error instanceof ApiError && document.error.code === 'unknown_id';
    return (
      <div className={styles.empty} role={document.isError ? 'alert' : undefined}>
        {gone && version !== undefined ? (
          <>
            This document has no version {version}.{' '}
            <AppButton variant="link" onClick={() => show({ fragment })}>
              show latest
            </AppButton>
          </>
        ) : gone ? (
          `Document ${documentId} is gone.`
        ) : document.isError ? (
          <>
            Could not load the document: {document.error.message}{' '}
            <AppButton icon={actionIcon('retry')} onClick={() => document.refetch()}>
              Retry
            </AppButton>
          </>
        ) : (
          'Loading…'
        )}
      </div>
    );
  }

  const latest = doc.versions.at(-1)?.version ?? doc.version;
  // An earlier version is read only: no dock, no comment, no decision.
  const earlier = version !== undefined && version !== latest;

  const header = (
    <header className={styles.header}>
      <div className={styles.line}>
        <span className={`${styles.task} wrap`} title={task ? taskLabelTitle(task) : undefined}>
          {task ? qualifiedTaskLabel(task) : doc.taskId}
        </span>
        {phase && <span className={styles.phase}>{phaseLabel(phase)}</span>}
        <span className={`${styles.title} wrap`}>{doc.title}</span>
        {doc.versions.length > 1 ? (
          <VersionPicker
            versions={doc.versions}
            shown={doc.version}
            onPick={(picked) => show({ version: picked, fragment })}
          />
        ) : (
          <span className={styles.version}>v{doc.version}</span>
        )}
        <span className={styles.status} data-status={doc.status}>
          {describeDocumentStatus(doc.status)}
        </span>
      </div>
      <div className={styles.taskLine}>
        {task && <span className={`${styles.taskTitle} wrap`}>{task.title}</span>}
        {agent && (
          <PanelLink tab={sessionTab(agent.id)} className={styles.sessionLink}>
            Session
          </PanelLink>
        )}
        {doc.ref !== null && (
          <AppButton
            variant="quiet"
            icon={actionIcon(doc.archived ? 'reopen' : 'archive')}
            disabled={archiveDocument.isPending}
            onClick={() => archiveDocument.mutateAsync({ documentId, archived: !doc.archived })}
          >
            {doc.archived ? 'Unarchive' : 'Archive'}
          </AppButton>
        )}
      </div>
      {earlier && (
        <p className={styles.note} data-testid="earlier-version">
          Earlier version —{' '}
          <AppButton variant="link" onClick={() => show({ fragment })}>
            show latest
          </AppButton>
        </p>
      )}
      {missing && missing === fragment && (
        <p className={styles.note} data-testid="missing-fragment">
          No heading #{missing} in this version
        </p>
      )}
      {members.length > 1 && (
        <SiblingNav title={row!.group.title} members={members} documentId={documentId} />
      )}
    </header>
  );
  return (
    <div className={styles.document} data-phase={phase ?? undefined} data-testid="document-tab">
      {chrome ? <ScreenSheet>{header}</ScreenSheet> : header}
      <div className={styles.split}>
        <div className={styles.body} ref={body} data-testid="document-body">
          <div className={styles.column}>
            <Markdown source={doc.markdown} className={styles.prose} headingIds />
            {doc.ref !== null && (
              <ReferencedBy
                documentId={documentId}
                ownerTaskId={doc.taskId}
                links={doc.links}
                fragments={doc.fragments}
                onShowFragment={(target) => {
                  show({ version, fragment: target });
                  scrollTo(target);
                }}
              />
            )}
          </div>
        </div>
        {/* The margin is a 220px column beside the prose, which on a phone
            would leave the document unreadable. It draws no comment today —
            the server serves none — so hiding it loses nothing. */}
        {!narrow && !earlier && (
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
          answers the agent, never the document — see `orchestrator-ui/DESIGN.md`, "Review
          Dock", and `orchestrator-server.md`, "Commands". */}
      {!earlier && (
        <div className={styles.dock} data-waiting={waiting || undefined} data-testid="review-dock">
          {waiting ? (
            <DecisionCard agent={waiting} variant="dock" inDocumentId={documentId} />
          ) : (
            <ReviewActions
              doc={doc}
              members={Math.max(members.length, 1)}
              unresolved={0}
              onApprove={() => approveDocument.mutateAsync({ documentId, version: doc.version })}
              onRequestChanges={(summary) =>
                requestChanges.mutateAsync({ documentId, version: doc.version, summary })
              }
            />
          )}
        </div>
      )}
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

/**
 * The other members of the review group. In a narrow panel the list clamps to
 * the counter and two members, and a tap on it expands it.
 */
function SiblingNav({
  title,
  members,
  documentId,
}: {
  title: string;
  members: DocumentRow[];
  documentId: string;
}) {
  const { expanded, clamped, collapse, bodyProps } = useExpandableClamp([
    members.length,
    documentId,
  ]);
  return (
    <nav aria-label={title} className={styles.siblings}>
      <div className={styles.siblingsBody} data-clamped={clamped || undefined} {...bodyProps}>
        <span>
          {members.findIndex((d) => d.id === documentId) + 1} of {members.length}
        </span>
        {members
          .filter((d) => d.id !== documentId)
          .map((d) => (
            <PanelLink key={d.id} tab={documentTab(d.id)} className={styles.siblingLink}>
              <span className="truncate" title={d.title}>
                {d.title}
              </span>
            </PanelLink>
          ))}
      </div>
      {expanded && (
        <AppButton
          variant="link"
          className={styles.siblingsMore}
          aria-controls={bodyProps.id}
          onClick={collapse}
        >
          Show less
        </AppButton>
      )}
    </nav>
  );
}
