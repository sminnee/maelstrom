import { useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { ApiError } from '../api/http';
import { keys } from '../api/keys';
import { useWorld } from '../api/useWorld';
import { useWorktreeChanges, useWorktreeDiff } from '../api/worktreeChanges';
import type { FileDiff, WorktreeChanges } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { AppButton } from '../ui/AppButton';
import { DiffBlock, DiffRow } from '../ui/DiffRow';
import styles from './ChangesTab.module.css';

const UNCOMMITTED = 'uncommitted';
const BRANCH = 'branch';

/** Uncommitted when there is dirty work, else the whole branch. */
const defaultRev = (changes: WorktreeChanges) =>
  changes.dirtyFiles.length > 0 ? UNCOMMITTED : BRANCH;

/** The rev the user picked while it still names something, else the default. */
function revToShow(changes: WorktreeChanges, picked: string | null): string {
  const known =
    picked === UNCOMMITTED || picked === BRANCH || changes.commits.some((c) => c.sha === picked);
  return known && picked ? picked : defaultRev(changes);
}

/**
 * A worktree's changes: its dirty files, each commit its branch has over its
 * base, and the branch as one diff. The picker chooses which; the file list
 * jumps to a file; every file follows as one continuous list.
 */
export function ChangesTab({ worktreeId }: { worktreeId: WorktreeId }) {
  const { world } = useWorld();
  const queryClient = useQueryClient();
  const changes = useWorktreeChanges(worktreeId);
  const [picked, setPicked] = useState<string | null>(null);
  const rev = changes.data ? revToShow(changes.data, picked) : null;
  const diff = useWorktreeDiff(worktreeId, rev);
  const worktree = world.worktrees[worktreeId];

  if (!changes.data) {
    const gone = changes.error instanceof ApiError && changes.error.code === 'unknown_id';
    return (
      <div className={styles.empty} role={changes.isError ? 'alert' : undefined}>
        {gone ? (
          `Worktree ${worktreeId} is closed or gone.`
        ) : changes.isError ? (
          <>
            Could not read the changes: {changes.error.message}{' '}
            <AppButton onClick={() => changes.refetch()}>Retry</AppButton>
          </>
        ) : (
          'Reading the changes…'
        )}
      </div>
    );
  }

  const { dirtyFiles, commits, base } = changes.data;
  return (
    <div className={styles.tab} data-testid="changes-tab">
      <header className={styles.header}>
        <span className={styles.where}>
          <span className={styles.branch}>{worktree?.branch || worktreeId}</span>
          <span className={styles.base}>on {base}</span>
        </span>
        <select
          className={styles.picker}
          aria-label="Changes to show"
          value={rev ?? ''}
          onChange={(e) => setPicked(e.target.value)}
        >
          <option value={UNCOMMITTED}>Uncommitted ({dirtyFiles.length})</option>
          <option value={BRANCH}>All commits ({commits.length})</option>
          {commits.map((c) => (
            <option key={c.sha} value={c.sha}>
              {c.shortSha} {c.subject}
            </option>
          ))}
        </select>
        <AppButton
          variant="quiet"
          processingChildren="Refreshing"
          onClick={() =>
            queryClient.invalidateQueries({ queryKey: keys.worktreeChanges.of(worktreeId) })
          }
        >
          Refresh
        </AppButton>
      </header>
      {diff.data ? (
        <Files files={diff.data.files} empty={emptyText(rev, base)} />
      ) : (
        <div className={styles.empty} role={diff.isError ? 'alert' : undefined}>
          {diff.isError ? `Could not read the diff: ${diff.error.message}` : 'Reading the diff…'}
        </div>
      )}
    </div>
  );
}

function emptyText(rev: string | null, base: string): string {
  if (rev === UNCOMMITTED) return 'No uncommitted changes';
  if (rev === BRANCH) return `No commits ahead of ${base}`;
  return 'This commit changes no files';
}

/** The file list, then every file's diff, in one scroll. */
function Files({ files, empty }: { files: FileDiff[]; empty: string }) {
  const blocks = useRef(new Map<string, HTMLElement>());
  if (files.length === 0) return <div className={styles.empty}>{empty}</div>;
  const additions = files.reduce((n, f) => n + f.additions, 0);
  const deletions = files.reduce((n, f) => n + f.deletions, 0);
  return (
    <div className={styles.scroll}>
      <details className={styles.list} open>
        <summary className={styles.summary}>
          {files.length} {files.length === 1 ? 'file' : 'files'}{' '}
          <Counts add={additions} remove={deletions} />
        </summary>
        <ul aria-label="Files">
          {files.map((f) => (
            <li key={f.path}>
              <button
                type="button"
                className={styles.entry}
                onClick={() => blocks.current.get(f.path)?.scrollIntoView({ block: 'start' })}
              >
                <span className={styles.status} data-status={f.status}>
                  {STATUS_LETTER[f.status]}
                </span>
                <span className={styles.path}>{f.path}</span>
                <Counts add={f.additions} remove={f.deletions} />
              </button>
            </li>
          ))}
        </ul>
      </details>
      {files.map((f) => (
        <section
          key={f.path}
          aria-label={f.path}
          className={styles.file}
          ref={(el) => {
            if (el) blocks.current.set(f.path, el);
            else blocks.current.delete(f.path);
          }}
        >
          <FileBlock file={f} />
        </section>
      ))}
    </div>
  );
}

const STATUS_LETTER: Record<FileDiff['status'], string> = {
  added: 'A',
  modified: 'M',
  deleted: 'D',
  renamed: 'R',
};

function Counts({ add, remove }: { add: number; remove: number }) {
  return (
    <span className={styles.counts}>
      <span className={styles.add}>+{add}</span> <span className={styles.remove}>−{remove}</span>
    </span>
  );
}

function FileBlock({ file }: { file: FileDiff }) {
  return (
    <>
      <h3 className={styles.fileHead}>
        <span className={styles.status} data-status={file.status}>
          {STATUS_LETTER[file.status]}
        </span>
        <span className={styles.path}>
          {file.oldPath ? `${file.oldPath} → ${file.path}` : file.path}
        </span>
        <Counts add={file.additions} remove={file.deletions} />
      </h3>
      {file.binary ? (
        <p className={styles.note}>Binary file, not shown.</p>
      ) : (
        <DiffBlock>
          {file.hunks.map((hunk, h) => (
            <div key={h}>
              <div className={styles.hunk}>{hunk.header}</div>
              {hunk.lines.map((line, i) => (
                <DiffRow
                  key={i}
                  kind={line.kind}
                  text={line.text}
                  lineNumbers={{ old: line.oldLine, new: line.newLine }}
                />
              ))}
            </div>
          ))}
        </DiffBlock>
      )}
      {file.truncated && (
        <p className={styles.note}>This file is too long to show whole. The rest is cut.</p>
      )}
    </>
  );
}
