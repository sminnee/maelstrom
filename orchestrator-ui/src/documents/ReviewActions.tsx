import { useState } from 'react';
import type { Document } from '../protocol/documents';
import { describeError } from '../api/http';
import { AppButton } from '../ui/AppButton';
import { TextArea } from '../ui/TextArea';
import styles from './ReviewActions.module.css';

/**
 * What Approve does, said plainly. Approving a task set writes the drafts into
 * the notebook, so the button names that rather than reading as mere agreement.
 * For a review group, the label counts its members.
 */
function approveLabel(doc: Document, members: number) {
  if (doc.kind === 'tasks' && doc.source.type === 'draft_file') {
    return members > 1 ? `Approve and create ${members} tasks` : 'Approve and create tasks';
  }
  return members > 1 ? `Approve all ${members}` : 'Approve';
}

export function ReviewActions({
  doc,
  members = 1,
  unresolved,
  onApprove,
  onRequestChanges,
}: {
  doc: Document;
  /** How many current members `doc`'s review group has, `doc` among them. */
  members?: number;
  unresolved: number;
  onApprove: () => void | Promise<unknown>;
  onRequestChanges: (summary: string) => void | Promise<unknown>;
}) {
  const [summary, setSummary] = useState('');
  // A draft blocks nothing, so it gets no review bar.
  if (doc.status === 'draft') return null;
  if (doc.status !== 'awaiting-review') {
    return (
      <div className={styles.bar}>
        {members > 1 ? `All ${members} are ${doc.status}.` : `This version is ${doc.status}.`}
      </div>
    );
  }
  // Approve leads, as the primary — see `orchestrator-ui/DESIGN.md`, "Review Dock".
  return (
    <div className={styles.bar}>
      <AppButton variant="primary" errorChildren={describeError} onClick={() => onApprove()}>
        {approveLabel(doc, members)}
      </AppButton>
      <TextArea
        grow
        rows={1}
        aria-label="Summary of requested changes"
        placeholder={
          unresolved ? `${unresolved} comment(s) go back with this` : 'Summary of requested changes'
        }
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
      />
      <AppButton
        errorChildren={describeError}
        disabled={!summary.trim() && unresolved === 0}
        onClick={() => onRequestChanges(summary.trim())}
      >
        {members > 1 ? `Request changes on all ${members}` : 'Request changes'}
      </AppButton>
    </div>
  );
}
