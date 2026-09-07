import { useId, useMemo } from 'react';
import { useLinearIssues } from '../api/linear';
import { ComboBox } from '../ui/ComboBox';
import { Spinner } from '../ui/Spinner';
import dialog from '../ui/Dialog.module.css';
import styles from './NewWork.module.css';

/**
 * Step 1 for the Linear kind: which issue to plan, and nothing else.
 *
 * Planning an issue is `mael linear plan`, which takes only the issue — the
 * brief, the branch, the command and the mode all come from it. So there is one
 * field here, against the three a free agent needs.
 */
export function LinearFields({
  project,
  issue,
  setIssue,
}: {
  project: string;
  issue: string;
  setIssue: (issue: string) => void;
}) {
  const issueId = useId();
  const issues = useLinearIssues(project);

  const options = useMemo(
    () => (issues.data?.issues ?? []).map((i) => ({ value: i.id, label: i.title })),
    [issues.data],
  );

  return (
    <div className={dialog.field}>
      <label htmlFor={issueId}>Issue</label>
      <ComboBox
        id={issueId}
        value={issue}
        options={options}
        onChange={setIssue}
        placeholder={issues.isPending ? 'Reading the cycle…' : 'Choose an issue'}
      />
      {issues.isPending && <Spinner />}
      {issues.error && (
        <p className={styles.error} role="alert">
          {issues.error.message}
        </p>
      )}
    </div>
  );
}
