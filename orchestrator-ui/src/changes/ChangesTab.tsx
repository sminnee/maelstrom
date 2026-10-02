import { useQueryClient } from '@tanstack/react-query';
import {
  type Dispatch,
  type ReactNode,
  type SetStateAction,
  useCallback,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import { ApiError } from '../api/http';
import { keys } from '../api/keys';
import { useWorld } from '../api/useWorld';
import {
  type PostedComments,
  usePostChangeComments,
  useWorktreeChanges,
  useWorktreeDiff,
} from '../api/worktreeChanges';
import { Markdown } from '../markdown/Markdown';
import type { BranchCommit, FileDiff, WorktreeChanges } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { clockTime } from '../protocol/time';
import { AppButton } from '../ui/AppButton';
import { trackedAgents } from '../selectors/worktrees';
import { DiffBlock, type DiffRowData, HighlightedRows } from '../ui/DiffRow';
import { retainedKey } from '../ui/retained';
import { useRetained } from '../ui/useRetained';
import { useNow } from '../ui/useNow';
import { ChangeCommentBox } from './comments/ChangeCommentBox';
import { CommentDock } from './comments/CommentDock';
import {
  cancelled,
  editing,
  editWouldDiscard,
  NO_COMMENTS,
  openOn,
  withBody,
  withOpenAdded,
  without,
} from './comments/held';
import { fileRows, type HeldComments, placeOf, spanLabel, spanOf } from './comments/selection';
import { useLineSelection } from './comments/useLineSelection';
import commentStyles from './comments/comments.module.css';
import { STATUS_LETTER } from './fileStatus';
import { FileTree } from './FileTree';
import styles from './ChangesTab.module.css';

const UNCOMMITTED = 'uncommitted';
const BRANCH = 'branch';

/**
 * The first commit when the branch has commits, else the uncommitted changes.
 * With neither, the whole branch: its empty text names the base.
 */
const defaultRev = (changes: WorktreeChanges) =>
  changes.commits[0]?.sha ?? (changes.dirtyFiles.length > 0 ? UNCOMMITTED : BRANCH);

/** The rev the user picked while the strip still lists it, else the default. */
function revToShow(changes: WorktreeChanges, picked: string | null): string {
  const known =
    (picked === UNCOMMITTED && changes.dirtyFiles.length > 0) ||
    (picked === BRANCH && changes.commits.length > 0) ||
    changes.commits.some((c) => c.sha === picked);
  return known && picked ? picked : defaultRev(changes);
}

/**
 * A worktree's changes: its dirty files, each commit its branch has over its
 * base, and the branch as one diff. The strip chooses which; the file list
 * and the file tree jump to a file; every file follows as one continuous list.
 * The user comments on lines of any rev, and one post sends every comment to
 * the agents in the worktree.
 */
export function ChangesTab({ worktreeId }: { worktreeId: WorktreeId }) {
  const { world } = useWorld();
  const queryClient = useQueryClient();
  const changes = useWorktreeChanges(worktreeId);
  const [picked, setPicked] = useState<string | null>(null);
  const rev = changes.data ? revToShow(changes.data, picked) : null;
  const diff = useWorktreeDiff(worktreeId, rev);
  const worktree = world.worktrees[worktreeId];
  // The folds live here, not beside the lines they fold: the scroll is keyed
  // on the rev, and a fold stays from one commit to the next.
  const [messageOpen, setMessageOpen] = useState(true);
  const [listOpen, setListOpen] = useState(true);
  const blocks = useRef(new Map<string, HTMLElement>());
  const holdFile = (path: string, el: HTMLElement | null) => {
    if (el) blocks.current.set(path, el);
    else blocks.current.delete(path);
  };
  const scrollToFile = (path: string) =>
    blocks.current.get(path)?.scrollIntoView({ block: 'start' });
  const [held, setHeld, clear] = useRetained(retainedKey.changeComments(worktreeId), NO_COMMENTS);
  const post = usePostChangeComments();
  // The agents a post did not reach. The comments are gone by then, so the dock is too.
  const [missed, setMissed] = useState<PostedComments['refused']>([]);
  // The user knows the task, not the agent id. A free agent has only its id.
  const nameOf = (agentId: string) =>
    world.tasks[world.agents[agentId]?.taskId ?? '']?.title || agentId;
  const recipients = trackedAgents(world, worktreeId).map((agent) => nameOf(agent.id));
  const postComments = async () => {
    const sent = held.comments;
    const posted = await post.mutateAsync({ worktreeId, comments: sent });
    // Only what was sent goes. The user can write while the post runs.
    const ids = sent.map((c) => c.id);
    setHeld((h) => without(h, ids));
    setMissed(posted.refused);
  };

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
  const at = commits.findIndex((c) => c.sha === rev);
  return (
    <div className={styles.tab} data-testid="changes-tab">
      <header className={styles.header}>
        <span className={styles.where}>
          <span className={styles.branch}>{worktree?.branch || worktreeId}</span>
          <span className={styles.base}>on {base}</span>
        </span>
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
      <div className={styles.body}>
        {(dirtyFiles.length > 0 || commits.length > 0) && (
          <nav className={styles.strip} aria-label="Changes to show">
            {commits.length > 0 && (
              <ul className={styles.revs}>
                {commits.map((c) => (
                  <RevEntry
                    key={c.sha}
                    rev={c.sha}
                    current={rev}
                    onPick={setPicked}
                    title={c.subject}
                  >
                    <span className={styles.sha}>{c.shortSha}</span>{' '}
                    <span className={styles.revLabel}>{c.subject}</span>
                  </RevEntry>
                ))}
              </ul>
            )}
            <ul className={`${styles.revs} ${styles.summary}`}>
              {commits.length > 0 && (
                <RevEntry rev={BRANCH} current={rev} onPick={setPicked}>
                  <span className={styles.revLabel}>All commits</span>{' '}
                  <span className={styles.revCount}>{commits.length}</span>
                </RevEntry>
              )}
              {dirtyFiles.length > 0 && (
                <RevEntry rev={UNCOMMITTED} current={rev} onPick={setPicked}>
                  <span className={styles.revLabel}>Uncommitted</span>{' '}
                  <span className={styles.revCount}>{dirtyFiles.length}</span>
                </RevEntry>
              )}
            </ul>
            {diff.data && diff.data.files.length > 0 && (
              <FileTree key={rev} files={diff.data.files} onPick={scrollToFile} />
            )}
          </nav>
        )}
        {diff.data ? (
          <Files
            // A new rev opens at the top, not at the last rev's scroll position.
            key={rev}
            rev={diff.data.rev}
            held={held}
            setHeld={setHeld}
            files={diff.data.files}
            empty={emptyText(rev, base)}
            commit={commits[at]}
            prev={commits[at - 1]?.sha}
            next={commits[at + 1]?.sha}
            onPick={setPicked}
            holdFile={holdFile}
            scrollToFile={scrollToFile}
            messageOpen={messageOpen}
            onMessageOpen={setMessageOpen}
            listOpen={listOpen}
            onListOpen={setListOpen}
          />
        ) : (
          <div className={styles.empty} role={diff.isError ? 'alert' : undefined}>
            {diff.isError ? `Could not read the diff: ${diff.error.message}` : 'Reading the diff…'}
          </div>
        )}
      </div>
      {held.comments.length > 0 && (
        <CommentDock
          count={held.comments.length}
          recipients={recipients}
          onPost={postComments}
          onClear={clear}
        />
      )}
      {missed.length > 0 && held.comments.length === 0 && (
        <div className={commentStyles.notice} role="status">
          <span>
            Posted, but not to {missed.map((m) => `${nameOf(m.agentId)} (${m.message})`).join(', ')}
            .
          </span>
          <AppButton variant="quiet" onClick={() => setMissed([])}>
            Dismiss
          </AppButton>
        </div>
      )}
    </div>
  );
}

function RevEntry({
  rev,
  current,
  onPick,
  title,
  children,
}: {
  rev: string;
  current: string | null;
  onPick: (rev: string) => void;
  title?: string;
  children: ReactNode;
}) {
  return (
    <li>
      <button
        type="button"
        className={styles.rev}
        aria-current={rev === current ? 'true' : undefined}
        title={title}
        onClick={() => onPick(rev)}
      >
        {children}
      </button>
    </li>
  );
}

/** A button that folds the region `controls` names. The chevron is drawn in CSS. */
function Fold({
  open,
  onOpen,
  controls,
  children,
}: {
  open: boolean;
  onOpen: (open: boolean) => void;
  controls: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={styles.fold}
      aria-expanded={open}
      aria-controls={controls}
      onClick={() => onOpen(!open)}
    >
      {children}
    </button>
  );
}

/**
 * A commit's body, and who wrote it when. The subject is on the title line
 * above. The body is drawn as Markdown: that joins git's hard wraps and draws
 * the lists and code spans agents write.
 */
function CommitMessage({ commit, id, open }: { commit: BranchCommit; id: string; open: boolean }) {
  const now = useNow();
  return (
    <article id={id} className={styles.message} aria-label="Commit message" hidden={!open}>
      {commit.body && <Markdown source={commit.body} className={styles.messageBody} />}
      <p className={styles.byline}>
        <span className={styles.sha}>{commit.shortSha}</span> {commit.author},{' '}
        <time dateTime={commit.date}>{clockTime(commit.date, now)}</time>
      </p>
    </article>
  );
}

function emptyText(rev: string | null, base: string): string {
  if (rev === UNCOMMITTED) return 'No uncommitted changes';
  if (rev === BRANCH) return `No commits ahead of ${base}`;
  return 'This commit changes no files';
}

/**
 * One scroll: the commit, the file list, then every file's diff. The title
 * line and the stats line are direct children of the scroll, because a sticky
 * element holds only inside its parent.
 */
function Files({
  rev,
  held,
  setHeld,
  files,
  empty,
  commit,
  prev,
  next,
  onPick,
  holdFile,
  scrollToFile,
  messageOpen,
  onMessageOpen,
  listOpen,
  onListOpen,
}: {
  rev: string;
  held: HeldComments;
  setHeld: Dispatch<SetStateAction<HeldComments>>;
  files: FileDiff[];
  empty: string;
  commit: BranchCommit | undefined;
  /** The shas of the commit before and the commit after, where there is one. */
  prev: string | undefined;
  next: string | undefined;
  onPick: (rev: string) => void;
  holdFile: (path: string, el: HTMLElement | null) => void;
  scrollToFile: (path: string) => void;
  messageOpen: boolean;
  onMessageOpen: (open: boolean) => void;
  listOpen: boolean;
  onListOpen: (open: boolean) => void;
}) {
  const messageId = useId();
  const listId = useId();
  const additions = files.reduce((n, f) => n + f.additions, 0);
  const deletions = files.reduce((n, f) => n + f.deletions, 0);
  const rows = useMemo(() => new Map(files.map((f) => [f.path, fileRows(f)])), [files]);
  const select = useCallback(
    (path: string, from: number, to: number) => {
      const span = spanOf(rows.get(path) ?? [], from, to);
      if (!span) return;
      setHeld((h) => openOn(h, rev, path, span));
    },
    [rows, rev, setHeld],
  );
  const cancel = useCallback(() => setHeld(cancelled), [setHeld]);
  const selection = useLineSelection(select, cancel);
  return (
    <div
      className={styles.scroll}
      data-commit={commit !== undefined}
      data-selecting={selection.dragging || undefined}
    >
      {commit && (
        <>
          <div className={styles.titleLine}>
            <Fold open={messageOpen} onOpen={onMessageOpen} controls={messageId}>
              <span className={styles.subject}>{commit.subject}</span>
            </Fold>
            <AppButton
              variant="quiet"
              aria-label="Previous commit"
              disabled={!prev}
              onClick={() => {
                if (prev) onPick(prev);
              }}
            >
              Prev
            </AppButton>
            <AppButton
              variant="quiet"
              aria-label="Next commit"
              disabled={!next}
              onClick={() => {
                if (next) onPick(next);
              }}
            >
              Next
            </AppButton>
          </div>
          <CommitMessage commit={commit} id={messageId} open={messageOpen} />
        </>
      )}
      {files.length === 0 && <div className={styles.empty}>{empty}</div>}
      {files.length > 0 && (
        <>
          <div className={styles.statsLine}>
            <Fold open={listOpen} onOpen={onListOpen} controls={listId}>
              {files.length} {files.length === 1 ? 'file' : 'files'}{' '}
              <Counts add={additions} remove={deletions} />
            </Fold>
          </div>
          <ul id={listId} className={styles.list} aria-label="Files" hidden={!listOpen}>
            {files.map((f) => (
              <li key={f.path}>
                <button type="button" className={styles.entry} onClick={() => scrollToFile(f.path)}>
                  <span className={styles.status} data-status={f.status}>
                    {STATUS_LETTER[f.status]}
                  </span>
                  <span className={styles.path}>{f.path}</span>
                  <Counts add={f.additions} remove={f.deletions} />
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {files.map((f) => (
        <section
          key={f.path}
          aria-label={f.path}
          className={styles.file}
          ref={(el) => holdFile(f.path, el)}
        >
          <FileBlock
            file={f}
            rows={rows.get(f.path) ?? []}
            rev={rev}
            held={held}
            setHeld={setHeld}
            dragged={selection.dragged(f.path)}
            handlers={selection.handlers}
          />
        </section>
      ))}
    </div>
  );
}

function Counts({ add, remove }: { add: number; remove: number }) {
  return (
    <span className={styles.counts}>
      <span className={styles.add}>+{add}</span> <span className={styles.remove}>−{remove}</span>
    </span>
  );
}

function FileBlock({
  file,
  rows,
  rev,
  held,
  setHeld,
  dragged,
  handlers,
}: {
  file: FileDiff;
  /** Every row of the file, across its hunks. A selection counts rows in this list. */
  rows: DiffRowData[];
  rev: string;
  held: HeldComments;
  setHeld: Dispatch<SetStateAction<HeldComments>>;
  /** The rows the drag in progress covers, when it is in this file. */
  dragged: [number, number] | null;
  handlers: ReturnType<typeof useLineSelection>['handlers'];
}) {
  // A comment draws below its last row. One whose lines are not in this diff
  // has no place, and stays in the dock's count only.
  const here = <T extends { rev: string; path: string }>(c: T | null): c is T =>
    c !== null && c.rev === rev && c.path === file.path;
  const open = here(held.open) ? held.open : null;
  const openAt = open && placeOf(rows, open);
  const added = held.comments
    .filter((c) => here(c) && c.id !== held.open?.id)
    .flatMap((c) => {
      const at = placeOf(rows, c);
      return at ? [{ comment: c, last: at[1] }] : [];
    });
  const lit = dragged ?? openAt;
  const gutter = (i: number) => ({
    selected: lit !== null && i >= lit[0] && i <= lit[1],
    handlers: handlers(file.path, i),
  });
  const after = (i: number) => (
    <>
      {added
        .filter((a) => a.last === i)
        .map(({ comment }) => (
          <ChangeCommentBox
            key={comment.id}
            label={spanLabel(comment)}
            body={comment.body}
            editDisabled={editWouldDiscard(held, comment.id)}
            onEdit={() => setHeld((h) => editing(h, comment))}
            onDelete={() => setHeld((h) => without(h, [comment.id]))}
          />
        ))}
      {open && openAt?.[1] === i && (
        <ChangeCommentBox
          label={spanLabel(open)}
          body={open.body}
          onBody={(body) => setHeld((h) => withBody(h, body))}
          onAdd={() => setHeld(withOpenAdded)}
          onCancel={() => setHeld(cancelled)}
        />
      )}
    </>
  );
  // Where each hunk's rows begin in `rows`.
  const starts = file.hunks.map((_, h) =>
    file.hunks.slice(0, h).reduce((n, hunk) => n + hunk.lines.length, 0),
  );
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
        <DiffBlock className={styles.diff}>
          <div className={styles.rows}>
            {file.hunks.map((hunk, h) => {
              const first = starts[h]!;
              return (
                <div key={h}>
                  <div className={styles.hunk}>{hunk.header}</div>
                  <HighlightedRows
                    path={file.path}
                    rows={rows.slice(first, first + hunk.lines.length)}
                    gutter={(i) => gutter(first + i)}
                    after={(i) => after(first + i)}
                  />
                </div>
              );
            })}
          </div>
        </DiffBlock>
      )}
      {file.truncated && (
        <p className={styles.note}>This file is too long to show whole. The rest is cut.</p>
      )}
    </>
  );
}
