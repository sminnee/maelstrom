import { useState } from 'react';
import type { Document } from '../protocol/documents';
import { describeError } from '../api/http';
import { AppButton } from '../ui/AppButton';
import styles from './ReviewActions.module.css';

export function ReviewActions({
  doc,
  unresolved,
  onApprove,
  onRequestChanges,
}: {
  doc: Document;
  unresolved: number;
  onApprove: () => void | Promise<unknown>;
  onRequestChanges: (summary: string) => void | Promise<unknown>;
}) {
  const [summary, setSummary] = useState('');
  if (doc.status !== 'awaiting-review') {
    return <div className={styles.bar}>This version is {doc.status}.</div>;
  }
  return (
    <div className={styles.bar}>
      <input
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
        Request changes
      </AppButton>
      <AppButton
        className={styles.approve}
        errorChildren={describeError}
        onClick={() => onApprove()}
      >
        Approve
      </AppButton>
    </div>
  );
}
