import { Link } from 'react-router';
import { IN_APP } from '../nav/useNav';
import { useDocuments } from '../api/documents';
import { driftLabel } from '../protocol/progress';
import { phaseLabel } from '../protocol/phase';
import type { GraphNode } from '../selectors/graph';
import { nodeIdLine, nodeTitle } from '../selectors/graph';
import { cardPr } from '../selectors/cardPr';
import { documentTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { PrChip } from '../shell/PrChip';
import styles from './DeckRow.module.css';

/**
 * One node in the deck list.
 *
 * It reads the same three registers the canvas node does — the title, the
 * state in words, then the identity — and carries the same `data-state` and
 * `data-phase`, so it inherits the node's whole state vocabulary rather than
 * inventing a second one. The row is a link: a tap opens the node.
 */
export function DeckRow({
  node,
  showProject,
  to,
}: {
  node: GraphNode;
  /** Whether the row names its project. False when the project filter already does. */
  showProject: boolean;
  /** Where a tap goes: the node's card, which the narrow layout draws as its detail. */
  to: string;
}) {
  const documentId = node.attention.find((a) => a.documentId)?.documentId;
  const documents = useDocuments();
  const documentTitle = documentId
    ? documents.data?.documents.find((d) => d.id === documentId)?.title
    : undefined;
  return (
    <div
      className={styles.row}
      data-testid="deck-row"
      data-task-id={node.id}
      data-phase={node.phase ?? undefined}
      data-state={node.progress.state}
    >
      <Link to={to} state={IN_APP} className={styles.open}>
        <span className={styles.title}>{nodeTitle(node)}</span>
        <span className={styles.status}>
          <span className={styles.dot} aria-hidden="true" />
          <span className={styles.state}>{node.reason || node.progress.words}</span>
          {node.progress.drift && (
            <span
              className={styles.drift}
              role="img"
              aria-label={driftLabel(node.progress)}
              data-drift={node.progress.drift}
            >
              ▲
            </span>
          )}
        </span>
        <span className={styles.meta}>
          {showProject && node.task && <span className={styles.project}>{node.task.project}</span>}
          <span className={styles.id}>{nodeIdLine(node)}</span>
          {node.worktree && <span className={styles.worktree}>{node.worktree.nato}</span>}
          <PrChip pr={cardPr(node)} link={false} className={styles.pr} />
          {node.phase && <span className={styles.phase}>{phaseLabel(node.phase)}</span>}
        </span>
      </Link>
      {/* The badge sits outside the row's own link: it opens the document
          the agent waits on, which is a different destination. */}
      {node.progress.state === 'needs-attention' && documentId && (
        <PanelLink
          tab={documentTab(documentId)}
          className={styles.badge}
          aria-label={`needs attention: open ${documentTitle ?? 'the document'}`}
          icon={false}
        >
          !
        </PanelLink>
      )}
    </div>
  );
}
