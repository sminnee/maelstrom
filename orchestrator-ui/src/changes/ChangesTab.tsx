import { useQueryClient } from '@tanstack/react-query';
import {
  type Dispatch,
  memo,
  type ReactNode,
  type SetStateAction,
  useCallback,
  useId,
  useMemo,
  useEffect,
  useRef,
  useState,
} from 'react';
import { flushSync } from 'react-dom';
import { ApiError } from '../api/http';
import { useSyncWorktree } from '../api/worktrees';
import { keys } from '../api/keys';
import { useWorld } from '../api/useWorld';
import {
  type DiffBody,
  type PostedComments,
  useDefinition,
  usePostChangeComments,
  useWorktreeChanges,
  useWorktreeDiff,
  useWorktreeExcerpts,
} from '../api/worktreeChanges';
import { Markdown } from '../markdown/Markdown';
import { rendererFor } from '../rendered/plugins';
import { RenderedFileDialog } from '../rendered/RenderedFileDialog';
import type {
  BranchCommit,
  ChangeComment,
  Definition,
  DefinitionTarget,
  FileDiff,
  FileExcerpt,
  FileGroup,
  LineRange,
  Worktree,
  WorktreeChanges,
} from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { clockTime } from '../protocol/time';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { trackedAgents } from '../selectors/worktrees';
import { ChunkedRows, type RowBreak } from '../ui/ChunkedRows';
import { DiffBlock, type NameClick } from '../ui/DiffRow';
import { retainedKey } from '../ui/retained';
import { ScreenSheet, ScreenStrip } from '../shell/ScreenChrome';
import { useScreenChrome } from '../shell/screenChromeContext';
import { useRetained } from '../ui/useRetained';
import { useNow } from '../ui/useNow';
import { ChangeCommentBox } from './comments/ChangeCommentBox';
import { CommentDock } from './comments/CommentDock';
import {
  cancelled,
  editing,
  NO_COMMENTS,
  openHoldsText,
  openOn,
  withBody,
  withOpenAdded,
  without,
} from './comments/held';
import {
  type HeldComments,
  type OpenComment,
  placeOf,
  spanLabel,
  spanOf,
} from './comments/selection';
import { clientId } from './clientId';
import { useLineSelection } from './comments/useLineSelection';
import commentStyles from './comments/comments.module.css';
import { STATUS_LETTER } from './fileStatus';
import { FileTree } from './FileTree';
import { type Card, type CardRows, cardRows, flow, type Mark, type Origin, type Pin } from './flow';
import styles from './ChangesTab.module.css';

const UNCOMMITTED = 'uncommitted';
const BRANCH = 'branch';

/** The fold keys: a group's, Pinned's and a file's. One map holds them all. */
const groupKey = (name: string) => `group:${name}`;
const PINNED_KEY = 'pinned';
const fileKey = (path: string) => `file:${path}`;

/** The pins of one rev, held in an object: `useRetained` merges a held object over its initial one. */
interface HeldPins {
  pins: Pin[];
}
const NO_PINS: HeldPins = { pins: [] };
const NO_PIN_PATHS: ReadonlySet<string> = new Set();

/** Whether a fold is open: the user's choice when there is one, else `fallback`. */
const isOpen = (folds: Map<string, boolean>, key: string, fallback: boolean) =>
  folds.get(key) ?? fallback;

/** The comments of a file that has none. One list, so the file's props stay the same. */
const NO_COMMENTS_HERE: ChangeComment[] = [];

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
 *
 * `Files`, `FileBlock` and `FileTree` are `memo`s, so pass them props that keep
 * their identity. See orchestrator-ui.md, "The Changes tab".
 */
export function ChangesTab({ worktreeId }: { worktreeId: WorktreeId }) {
  const { world } = useWorld();
  const queryClient = useQueryClient();
  const changes = useWorktreeChanges(worktreeId);
  const [picked, setPicked] = useState<string | null>(null);
  const rev = changes.data ? revToShow(changes.data, picked) : null;
  const diff = useWorktreeDiff(worktreeId, rev);
  const worktree = world.worktrees[worktreeId];
  // See DESIGN.md, "The One Strip Rule".
  const chrome = useScreenChrome();

  // The folds live here, not beside the lines they fold: the scroll is keyed
  // on the rev, and a fold stays from one commit to the next.
  const [messageOpen, setMessageOpen] = useState(true);
  const [listOpen, setListOpen] = useState(true);
  // The group and file folds the user chose, by `groupKey` and `fileKey`.
  const [folds, setFolds] = useState<Map<string, boolean>>(() => new Map());
  const setFold = useCallback(
    (key: string, open: boolean) => setFolds((f) => new Map(f).set(key, open)),
    [],
  );
  const [heldPins, setPins] = useRetained(
    rev === null ? null : retainedKey.pins(worktreeId, rev),
    NO_PINS,
  );
  const pins = heldPins.pins;
  const targets = useMemo(
    () => pins.map((p) => ({ id: p.id, path: p.target.path, range: p.target.range })),
    [pins],
  );
  const excerpts = useWorktreeExcerpts(worktreeId, rev ?? '', targets);
  const pinnedPaths = useMemo(
    () => (pins.length ? new Set(pins.map((p) => p.target.path)) : NO_PIN_PATHS),
    [pins],
  );
  const pin = useCallback(
    (path: string, range: LineRange | null, from: Origin | null = null) =>
      setPins((h) => ({
        pins: [...h.pins, { id: clientId('p'), target: { path, range }, from }],
      })),
    [setPins],
  );
  const unpin = useCallback(
    (ids: string[]) => setPins((h) => ({ pins: h.pins.filter((p) => !ids.includes(p.id)) })),
    [setPins],
  );
  const blocks = useRef(new Map<string, HTMLElement>());
  // A file picked with no card yet: it is pinned, and the scroll waits for its card. It
  // scrolls as the card is drawn, and again once its lines are read: the empty card at the
  // end of the flow cannot yet reach the top.
  const scrollWhenDrawn = useRef<string | null>(null);
  const holdFile = useCallback((path: string, el: HTMLElement | null) => {
    if (!el) {
      blocks.current.delete(path);
      return;
    }
    blocks.current.set(path, el);
    if (scrollWhenDrawn.current === path) el.scrollIntoView({ block: 'start' });
  }, []);
  useEffect(() => {
    const path = scrollWhenDrawn.current;
    if (!path || !pins.some((p) => p.target.path === path && excerpts.read.has(p.id))) return;
    scrollWhenDrawn.current = null;
    blocks.current.get(path)?.scrollIntoView({ block: 'start' });
  }, [pins, excerpts.read]);
  // Read at pick time, so `scrollToFile` keeps its identity across revs.
  const groups = useRef<FileGroup[]>([]);
  useEffect(() => {
    groups.current = diff.data?.groups ?? [];
  });
  // A folded file has no rows to land on, so a jump opens it and its group first.
  const reveal = useCallback((path: string) => {
    const group = groups.current.find((g) => g.paths.includes(path));
    setFolds((f) => {
      const next = new Map(f).set(fileKey(path), true);
      return group ? next.set(groupKey(group.name), true) : next.set(PINNED_KEY, true);
    });
  }, []);
  const scrollToFile = useCallback(
    (path: string) => {
      flushSync(() => reveal(path));
      const block = blocks.current.get(path);
      if (block) {
        block.scrollIntoView({ block: 'start' });
        return;
      }
      scrollWhenDrawn.current = path;
      pin(path, null);
    },
    [pin, reveal],
  ); // Stable, as the memo rule above asks: `onPickFile` reaches `FileTree`.
  const closeSheet = chrome?.closeSheet;
  const pickRevInSheet = useCallback(
    (picked: string) => {
      setPicked(picked);
      closeSheet?.();
    },
    [closeSheet],
  );
  const pickFileInSheet = useCallback(
    (path: string) => {
      closeSheet?.();
      scrollToFile(path);
    },
    [closeSheet, scrollToFile],
  );
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
            <AppButton icon={actionIcon('retry')} onClick={() => changes.refetch()}>
              Retry
            </AppButton>
          </>
        ) : (
          'Reading the changes…'
        )}
      </div>
    );
  }

  const { commits, base } = changes.data;
  const at = commits.findIndex((c) => c.sha === rev);
  const commit = commits[at];
  const prev = commits[at - 1]?.sha;
  const next = commits[at + 1]?.sha;
  const header = (
    <header className={styles.header}>
      <span className={styles.where}>
        <span className={`${styles.branch} truncate`} title={worktree?.branch || worktreeId}>
          {worktree?.branch || worktreeId}
        </span>
        <span className={`${styles.base} truncate`} title={`on ${base}`}>
          on {base}
        </span>
      </span>
      {worktree?.prNumber != null && worktree.prMatch && <PrMatch worktree={worktree} />}
      <AppButton
        icon={actionIcon('refresh')}
        variant="quiet"
        processingChildren="Refreshing"
        onClick={() =>
          queryClient.invalidateQueries({ queryKey: keys.worktreeChanges.of(worktreeId) })
        }
      >
        Refresh
      </AppButton>
    </header>
  );
  return (
    <div className={styles.tab} data-testid="changes-tab">
      {chrome ? (
        <>
          <ScreenStrip
            title={commit?.subject ?? (rev === UNCOMMITTED ? 'Uncommitted' : 'All commits')}
          >
            {commit && (
              <CommitStep prev={prev} next={next} onPick={setPicked} labels={['‹', '›']} />
            )}
          </ScreenStrip>
          <ScreenSheet>
            {header}
            {/* A pick in the side sheet closes it, so the diff it picked is in view. */}
            <RevNav
              worktreeId={worktreeId}
              changes={changes.data}
              rev={rev}
              files={diff.data?.files}
              pinned={pinnedPaths}
              onPickRev={pickRevInSheet}
              onPickFile={pickFileInSheet}
            />
          </ScreenSheet>
        </>
      ) : (
        header
      )}
      <div className={styles.body}>
        {!chrome && (
          <RevNav
            worktreeId={worktreeId}
            changes={changes.data}
            rev={rev}
            files={diff.data?.files}
            pinned={pinnedPaths}
            onPickRev={setPicked}
            onPickFile={scrollToFile}
          />
        )}
        {diff.data ? (
          <Files
            // A new rev opens at the top, not at the last rev's scroll position.
            key={rev}
            worktreeId={worktreeId}
            flat={!!chrome}
            rev={diff.data.rev}
            held={held}
            setHeld={setHeld}
            diff={diff.data}
            pins={pins}
            excerpts={excerpts.read}
            failed={excerpts.failed}
            onPin={pin}
            onUnpin={unpin}
            folds={folds}
            onFold={setFold}
            empty={emptyText(rev, base)}
            commit={commit}
            prev={prev}
            next={next}
            onPick={setPicked}
            holdFile={holdFile}
            scrollToFile={scrollToFile}
            reveal={reveal}
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
          <AppButton icon={actionIcon('dismiss')} variant="quiet" onClick={() => setMissed([])}>
            Dismiss
          </AppButton>
        </div>
      )}
    </div>
  );
}

/** The **PR match**, with a Sync beside it while it differs. */
function PrMatch({ worktree }: { worktree: Worktree }) {
  const sync = useSyncWorktree();
  const differs = worktree.prMatch === 'differ';
  return (
    <span
      role="group"
      aria-label="PR match"
      className={styles.prMatch}
      data-differs={differs || undefined}
    >
      <span>
        PR #{worktree.prNumber} {differs ? 'differs' : 'matches'}
      </span>
      {differs && (
        <AppButton
          icon={actionIcon('sync')}
          variant="quiet"
          processingChildren="Syncing…"
          onClick={() => sync.mutateAsync({ worktreeId: worktree.id, mode: 'plain' })}
        >
          Sync
        </AppButton>
      )}
    </span>
  );
}

/** The revs to show, and the file tree of the one in view. */
function RevNav({
  worktreeId,
  changes,
  rev,
  files,
  pinned,
  onPickRev,
  onPickFile,
}: {
  worktreeId: WorktreeId;
  changes: WorktreeChanges;
  rev: string | null;
  files: FileDiff[] | undefined;
  pinned: ReadonlySet<string>;
  onPickRev: (rev: string) => void;
  onPickFile: (path: string) => void;
}) {
  const { dirtyFiles, commits } = changes;
  if (dirtyFiles.length === 0 && commits.length === 0) return null;
  return (
    <nav className={styles.strip} aria-label="Changes to show">
      {commits.length > 0 && (
        <ul className={styles.revs}>
          {commits.map((c) => (
            <RevEntry key={c.sha} rev={c.sha} current={rev} onPick={onPickRev} title={c.subject}>
              <span className={styles.sha}>{c.shortSha}</span>{' '}
              <span className="truncate">{c.subject}</span>
            </RevEntry>
          ))}
        </ul>
      )}
      <ul className={`${styles.revs} ${styles.summary}`}>
        {commits.length > 0 && (
          <RevEntry rev={BRANCH} current={rev} onPick={onPickRev}>
            <span className="truncate">All commits</span>{' '}
            <span className={styles.revCount}>{commits.length}</span>
          </RevEntry>
        )}
        {dirtyFiles.length > 0 && (
          <RevEntry rev={UNCOMMITTED} current={rev} onPick={onPickRev}>
            <span className="truncate">Uncommitted</span>{' '}
            <span className={styles.revCount}>{dirtyFiles.length}</span>
          </RevEntry>
        )}
      </ul>
      {files && rev && (
        <FileTree
          key={rev}
          worktreeId={worktreeId}
          rev={rev}
          files={files}
          pinned={pinned}
          onPick={onPickFile}
        />
      )}
    </nav>
  );
}

/** Previous and Next commit. A button is disabled where there is no commit to step to. */
function CommitStep({
  prev,
  next,
  onPick,
  labels,
}: {
  prev: string | undefined;
  next: string | undefined;
  onPick: (rev: string) => void;
  labels: [string, string];
}) {
  return (
    <>
      <AppButton
        variant="quiet"
        aria-label="Previous commit"
        disabled={!prev}
        onClick={() => onPick(prev!)}
      >
        {labels[0]}
      </AppButton>
      <AppButton
        variant="quiet"
        aria-label="Next commit"
        disabled={!next}
        onClick={() => onPick(next!)}
      >
        {labels[1]}
      </AppButton>
    </>
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
 *
 * `flat` is the phone's screen: the strip carries the subject and Prev/Next,
 * so there is no title line, and the folds scroll away. Only a file's head
 * sticks.
 */
const Files = memo(function Files({
  worktreeId,
  flat,
  rev,
  held,
  setHeld,
  diff,
  pins,
  excerpts,
  failed,
  onPin,
  onUnpin,
  folds,
  onFold,
  empty,
  commit,
  prev,
  next,
  onPick,
  holdFile,
  scrollToFile,
  reveal,
  messageOpen,
  onMessageOpen,
  listOpen,
  onListOpen,
}: {
  worktreeId: WorktreeId;
  flat: boolean;
  rev: string;
  held: HeldComments;
  setHeld: Dispatch<SetStateAction<HeldComments>>;
  diff: DiffBody;
  pins: Pin[];
  /** The pins' lines, by pin id, and the pins whose read failed. */
  excerpts: Map<string, FileExcerpt>;
  failed: ReadonlySet<string>;
  onPin: (path: string, range: LineRange | null, from?: Origin | null) => void;
  onUnpin: (ids: string[]) => void;
  folds: Map<string, boolean>;
  onFold: (key: string, open: boolean) => void;
  empty: string;
  commit: BranchCommit | undefined;
  /** The shas of the commit before and the commit after, where there is one. */
  prev: string | undefined;
  next: string | undefined;
  onPick: (rev: string) => void;
  holdFile: (path: string, el: HTMLElement | null) => void;
  scrollToFile: (path: string) => void;
  /** Opens the folds over `path`'s card. */
  reveal: (path: string) => void;
  messageOpen: boolean;
  onMessageOpen: (open: boolean) => void;
  listOpen: boolean;
  onListOpen: (open: boolean) => void;
}) {
  const messageId = useId();
  const listId = useId();
  const { files } = diff;
  const additions = files.reduce((n, f) => n + f.additions, 0);
  const deletions = files.reduce((n, f) => n + f.deletions, 0);
  const { sections } = useMemo(() => flow(diff, pins, excerpts), [diff, pins, excerpts]);
  const rows = useMemo(
    () => new Map(sections.flatMap((s) => s.cards).map((c) => [c.path, cardRows(c)])),
    [sections],
  );
  const select = useCallback(
    (path: string, from: number, to: number) => {
      const span = spanOf(rows.get(path)?.rows ?? [], from, to);
      if (!span) return;
      setHeld((h) => openOn(h, rev, path, span));
    },
    [rows, rev, setHeld],
  );
  const cancel = useCallback(() => setHeld(cancelled), [setHeld]);
  const selection = useLineSelection(select, cancel);
  // Following a name: the line to scroll to once it is drawn, and why the last click went nowhere.
  const [goal, setGoal] = useState<{ path: string; line: number } | null>(null);
  const reached = useCallback(() => setGoal(null), []);
  const [notice, setNotice] = useState<{ path: string; text: string } | null>(null);
  const { mutateAsync: askDefinition } = useDefinition();
  // Read when the answer comes, so the flow is the one in view then.
  const cards = useRef<Card[]>([]);
  useEffect(() => {
    cards.current = sections.flatMap((s) => s.cards);
  });
  const go = useCallback(
    (target: DefinitionTarget, from: Origin) => {
      const { path, range } = target;
      const card = cards.current.find((c) => c.path === path);
      const drawn = card?.spans.some((s) => s.lines.some((l) => l.newLine === range.start));
      if (!drawn && card?.reason.kind === 'diff')
        onPin(path, { start: Math.max(1, range.start - 5), end: range.end + 25 }, from);
      else if (!drawn && !card?.pins.some((p) => p.target.range === null)) onPin(path, null, from);
      reveal(path);
      setGoal({ path, line: range.start });
    },
    [onPin, reveal],
  );
  const followName = useCallback(
    async (path: string, click: NameClick) => {
      setNotice(null);
      click.token.setAttribute('aria-busy', 'true');
      try {
        const answer = await askDefinition({
          worktreeId,
          rev,
          path,
          line: click.line,
          character: click.character,
        });
        const target = answer.targets.find((t) => t.inRev);
        if (target)
          go(target, {
            at: { path, range: { start: click.line, end: click.line } },
            name: click.name,
          });
        else setNotice({ path, text: noticeOf(answer, click.name) });
      } catch (e) {
        setNotice({ path, text: `Could not follow ${click.name}: ${(e as Error).message}` });
      } finally {
        click.token.removeAttribute('aria-busy');
      }
    },
    [askDefinition, worktreeId, rev, go],
  );
  // Each file's comments on this rev, less the one in the open box, which draws
  // as the box wherever the box is. A key in the box makes a new `held` with
  // the same `comments`, so the lists keep their identity through it.
  const openId = held.open?.id;
  const comments = useMemo(() => {
    const byPath = new Map<string, ChangeComment[]>();
    for (const c of held.comments) {
      if (c.rev !== rev || c.id === openId) continue;
      const list = byPath.get(c.path) ?? [];
      byPath.set(c.path, list);
      list.push(c);
    }
    return byPath;
  }, [held.comments, rev, openId]);
  const open = held.open?.rev === rev ? held.open : null;
  const editLocked = openHoldsText(held);
  // One dialog for the tab. `setViewing` keeps its identity, so opening a view
  // changes no prop of a `FileBlock` and draws no row again.
  const [viewing, setViewing] = useState<FileDiff | null>(null);
  // Only Other, open, and nothing pinned: the flow has no classes to tell apart, so no heads draw.
  const headed = sections.length > 1 || sections.some((s) => s.group?.collapsed);
  const listed = useMemo(
    () =>
      sections.flatMap((s) =>
        s.group
          ? [
              {
                name: s.group.name,
                files: s.cards.flatMap((c) => (c.reason.kind === 'diff' ? [c.reason.file] : [])),
              },
            ]
          : [],
      ),
    [sections],
  );
  const entry = (f: FileDiff) => (
    <li key={f.path} className={styles.entryRow}>
      <button type="button" className={styles.entry} onClick={() => scrollToFile(f.path)}>
        <span className={styles.status} data-status={f.status}>
          {STATUS_LETTER[f.status]}
        </span>
        <span className={`${styles.path} truncate`} title={f.path}>
          {f.path}
        </span>
        <Counts add={f.additions} remove={f.deletions} />
      </button>
      <ViewButton file={f} onView={setViewing} />
    </li>
  );
  const fileBlock = (card: Card) => {
    const here = comments.get(card.path);
    return (
      <FileBlock
        key={card.path}
        card={card}
        reading={card.total === null && card.pins.some((p) => !failed.has(p.id))}
        expanded={isOpen(folds, fileKey(card.path), true)}
        onFold={onFold}
        onView={setViewing}
        rows={rows.get(card.path)!}
        comments={here ?? NO_COMMENTS_HERE}
        open={open?.path === card.path ? open : null}
        // Only a file with comments draws Edit, so only it follows the lock.
        editLocked={here !== undefined && editLocked}
        setHeld={setHeld}
        dragged={selection.dragged(card.path)}
        handlersFor={selection.handlersFor}
        holdFile={holdFile}
        onPin={onPin}
        onUnpin={onUnpin}
        onName={card.navigable ? followName : undefined}
        notice={notice?.path === card.path ? notice.text : null}
        goalLine={goal?.path === card.path ? goal.line : null}
        onGoalReached={reached}
      />
    );
  };
  return (
    <div
      className={styles.scroll}
      data-commit={commit !== undefined}
      data-grouped={headed || undefined}
      data-flat={flat || undefined}
      data-selecting={selection.dragging || undefined}
    >
      {commit && flat && (
        <>
          <div className={styles.messageLine}>
            <Fold open={messageOpen} onOpen={onMessageOpen} controls={messageId}>
              <span className={`${styles.messageSubject} wrap`}>{commit.subject}</span>
            </Fold>
          </div>
          <CommitMessage commit={commit} id={messageId} open={messageOpen} />
        </>
      )}
      {commit && !flat && (
        <>
          <div className={styles.titleLine}>
            <Fold open={messageOpen} onOpen={onMessageOpen} controls={messageId}>
              <span className={`${styles.subject} truncate`} title={commit.subject}>
                {commit.subject}
              </span>
            </Fold>
            <CommitStep prev={prev} next={next} onPick={onPick} labels={['Prev', 'Next']} />
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
            {listed.length > 1 || sections.some((s) => s.group?.collapsed)
              ? listed.map((g) => (
                  <li key={g.name}>
                    <span className={styles.listGroup}>{g.name}</span>
                    <ul className={styles.listGroupFiles} aria-label={g.name}>
                      {g.files.map(entry)}
                    </ul>
                  </li>
                ))
              : files.map(entry)}
          </ul>
        </>
      )}
      {headed
        ? sections.map((s) => {
            const name = s.group?.name ?? 'Pinned';
            const key = s.group ? groupKey(name) : PINNED_KEY;
            return (
              <FileGroupSection
                key={key}
                name={name}
                cards={s.cards}
                open={isOpen(folds, key, !s.group?.collapsed)}
                onOpen={(o) => onFold(key, o)}
              >
                {s.cards.map(fileBlock)}
              </FileGroupSection>
            );
          })
        : sections.flatMap((s) => s.cards).map(fileBlock)}
      {viewing && (
        <RenderedFileDialog
          worktreeId={worktreeId}
          rev={rev}
          file={viewing}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
});

/**
 * One **Diff group**: a head that folds it, then its files. The head sticks
 * only while the group is in view, because a sticky element holds inside its
 * parent.
 */
function FileGroupSection({
  name,
  cards,
  open,
  onOpen,
  children,
}: {
  name: string;
  cards: Card[];
  open: boolean;
  onOpen: (open: boolean) => void;
  children: ReactNode;
}) {
  const id = useId();
  const files = cards.flatMap((c) => (c.reason.kind === 'diff' ? [c.reason.file] : []));
  const additions = files.reduce((n, f) => n + f.additions, 0);
  const deletions = files.reduce((n, f) => n + f.deletions, 0);
  return (
    <div role="group" aria-label={name} className={styles.group}>
      <div className={styles.groupHead}>
        <Fold open={open} onOpen={onOpen} controls={id}>
          <span className={styles.groupName}>{name}</span> {cards.length}{' '}
          {cards.length === 1 ? 'file' : 'files'}{' '}
          {files.length > 0 && <Counts add={additions} remove={deletions} />}
        </Fold>
      </div>
      <div id={id} hidden={!open}>
        {children}
      </div>
    </div>
  );
}

/** Opens the Rendered view of a file. A binary file has none, unless a plugin reads its bytes. */
function ViewButton({ file, onView }: { file: FileDiff; onView: (file: FileDiff) => void }) {
  // A binary file keeps the button's place, so the counts line up.
  if (file.binary && rendererFor(file.path).reads !== 'bytes')
    return <span className={styles.view} aria-hidden="true" />;
  return (
    <AppButton
      icon={actionIcon('view')}
      variant="quiet"
      className={styles.view}
      aria-label={`View ${file.path}`}
      title="View"
      onClick={() => onView(file)}
    />
  );
}

function Counts({ add, remove }: { add: number; remove: number }) {
  return (
    <span className={styles.counts}>
      <span className={styles.add}>+{add}</span> <span className={styles.remove}>−{remove}</span>
    </span>
  );
}

/** Why a followed name went nowhere: the notice its card shows. */
function noticeOf(answer: Definition, name: string): string {
  if (answer.reason === 'no_server') return 'No language server reads this file.';
  const target = answer.targets[0];
  return target ? `Definition not in this rev: ${target.path}` : `No definition of ${name} found.`;
}

/** `lines 4–9`, or nothing for a whole file: a pin's lines as its × names them. */
function pinLabel(p: Pin): string {
  const { path, range } = p.target;
  if (!range) return path;
  return range.end === Number.MAX_SAFE_INTEGER
    ? `${path} lines ${range.start} to the end`
    : `${path} lines ${range.start}–${range.end}`;
}

/** A mark drawn between a card's rows: a hunk's header, a gap that pins its lines, or a pin's ×. */
function markNode(
  path: string,
  mark: Mark,
  onPin: (path: string, range: LineRange) => void,
  onUnpin: (ids: string[]) => void,
) {
  if (mark.kind === 'hunk') return <div className={styles.hunk}>{mark.header}</div>;
  if (mark.kind === 'gap') {
    const end = mark.count === null ? null : mark.start + mark.count - 1;
    return (
      <button
        type="button"
        className={styles.gap}
        aria-label={
          end === null ? `Show lines ${mark.start} to the end` : `Show lines ${mark.start}–${end}`
        }
        // No count: the lines run to an end not known yet, and the server clips the range to it.
        onClick={() => onPin(path, { start: mark.start, end: end ?? Number.MAX_SAFE_INTEGER })}
      >
        {mark.count === null
          ? '⋯ to end of file'
          : `⋯ ${mark.count} ${mark.count === 1 ? 'line' : 'lines'}`}
      </button>
    );
  }
  return (
    <div className={styles.pinMark}>
      <button
        type="button"
        className={styles.unpin}
        aria-label={`Unpin ${pinLabel(mark.pin)}`}
        title="Unpin"
        onClick={() => onUnpin([mark.pin.id])}
      >
        ×
      </button>
    </div>
  );
}

/** One file: its head and its rows, with its comments below their lines. */
const FileBlock = memo(function FileBlock({
  card,
  reading,
  expanded,
  onFold,
  onView,
  rows: { rows, marks },
  comments,
  open,
  editLocked,
  setHeld,
  dragged,
  handlersFor,
  holdFile,
  onPin,
  onUnpin,
  onName,
  notice,
  goalLine,
  onGoalReached,
}: {
  card: Card;
  /** A pinned card whose lines are not read yet. */
  reading: boolean;
  /** Whether the rows show. A folded file keeps its head. */
  expanded: boolean;
  onFold: (key: string, open: boolean) => void;
  onView: (file: FileDiff) => void;
  /** Every row of the card, across its spans. A selection counts rows in this list. */
  rows: CardRows;
  /** The added comments on this file in the rev in view, less the one in the open box. */
  comments: ChangeComment[];
  /** The open box, when it is on this file in the rev in view. */
  open: OpenComment | null;
  /** Whether Edit would discard the text in the open box. */
  editLocked: boolean;
  setHeld: Dispatch<SetStateAction<HeldComments>>;
  /** The rows the drag in progress covers, when it is in this file. */
  dragged: [number, number] | null;
  handlersFor: ReturnType<typeof useLineSelection>['handlersFor'];
  holdFile: (path: string, el: HTMLElement | null) => void;
  onPin: (path: string, range: LineRange) => void;
  onUnpin: (ids: string[]) => void;
  /** Follows a name clicked in the card's rows. Only a card a **Language server** reads gets it. */
  onName: ((path: string, click: NameClick) => void) | undefined;
  /** Why the last name clicked here went nowhere. */
  notice: string | null;
  /** A new-side line to scroll to once it is drawn, then `onGoalReached`. */
  goalLine: number | null;
  onGoalReached: () => void;
}) {
  const { path } = card;
  const file = card.reason.kind === 'diff' ? card.reason.file : null;
  const handlers = useMemo(() => handlersFor(path), [handlersFor, path]);
  const sectionEl = useRef<HTMLElement | null>(null);
  const section = useCallback(
    (el: HTMLElement | null) => {
      sectionEl.current = el;
      holdFile(path, el);
    },
    [holdFile, path],
  );
  const followHere = useMemo(
    () => onName && ((click: NameClick) => onName(path, click)),
    [onName, path],
  );
  const goalIndex = goalLine === null ? -1 : rows.findIndex((r) => r.lineNumbers?.new === goalLine);
  useEffect(() => {
    if (goalIndex < 0 || !expanded) return;
    const row = sectionEl.current?.querySelector<HTMLElement>(`[data-new-line="${goalLine}"]`);
    if (!row) return;
    row.scrollIntoView({ block: 'center' });
    row.setAttribute('data-flash', '');
    row.addEventListener('animationend', () => row.removeAttribute('data-flash'), { once: true });
    onGoalReached();
  }, [goalIndex, goalLine, expanded, onGoalReached]);
  const bodyId = useId();
  const breaks = useMemo<RowBreak[]>(
    () =>
      marks.map((m, i) => ({
        key: `${m.kind}:${i}`,
        before: m.before,
        // A pin's × sits over its first row's gutter, so it takes no height.
        height: m.kind === 'pin' ? 0 : 1,
        node: markNode(path, m, onPin, onUnpin),
      })),
    [marks, path, onPin, onUnpin],
  );
  // A comment draws below its last row. One whose lines are not in this diff
  // has no place, and stays in the dock's count only. A place depends on the
  // lines and not on the text, so a key in the open box does not search again.
  const { side, startLine, endLine, lines } = open ?? {};
  const openAt = useMemo(
    () =>
      side !== undefined && startLine !== undefined && endLine !== undefined && lines
        ? placeOf(rows, { side, startLine, endLine, lines })
        : null,
    [rows, side, startLine, endLine, lines],
  );
  const added = useMemo(
    () =>
      comments.flatMap((c) => {
        const at = placeOf(rows, c);
        return at ? [{ comment: c, last: at[1] }] : [];
      }),
    [comments, rows],
  );
  // The rows whose chunks stay mounted out of view: a comment's, the open box's, the drag's.
  // And the row a followed name scrolls to, so it is drawn to land on.
  const hold = useMemo(
    () => [
      ...added.map((a) => a.last),
      ...(openAt ?? []),
      ...(dragged ?? []),
      ...(goalIndex >= 0 ? [goalIndex] : []),
    ],
    [added, openAt, dragged, goalIndex],
  );
  const after = (i: number) => (
    <>
      {added
        .filter((a) => a.last === i)
        .map(({ comment }) => (
          <ChangeCommentBox
            key={comment.id}
            label={spanLabel(comment)}
            body={comment.body}
            editDisabled={editLocked}
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
  const title = file?.oldPath ? `${file.oldPath} → ${path}` : path;
  return (
    <section aria-label={path} className={styles.file} ref={section}>
      <h3 className={styles.fileHead}>
        <Fold open={expanded} onOpen={(o) => onFold(fileKey(path), o)} controls={bodyId}>
          {file && (
            <span className={styles.status} data-status={file.status}>
              {STATUS_LETTER[file.status]}
            </span>
          )}
          <span className="truncate" title={title}>
            {title}
          </span>{' '}
          {file ? (
            <Counts add={file.additions} remove={file.deletions} />
          ) : (
            <span className={styles.notInDiff}>Not in this diff</span>
          )}
        </Fold>
        {file ? (
          <ViewButton file={file} onView={onView} />
        ) : (
          <button
            type="button"
            className={styles.unpinCard}
            aria-label={`Unpin ${path}`}
            title="Unpin"
            onClick={() => onUnpin(card.pins.map((p) => p.id))}
          >
            ×
          </button>
        )}
        {/* In the sticky head, so it shows wherever in the card the name was. */}
        {notice && (
          <span className={styles.nameNotice} role="status">
            {notice}
          </span>
        )}
      </h3>
      {/* `hidden`, not unmounted: the rows keep their place, so a selection's indices hold. */}
      <div id={bodyId} hidden={!expanded}>
        {file?.binary ? (
          <p className={styles.note}>Binary file, not shown.</p>
        ) : rows.length === 0 && !file ? (
          <p className={styles.note}>
            {reading
              ? 'Reading the lines…'
              : // A read sets the total, so a card with none failed to read.
                card.total === null
                ? 'Not in this rev.'
                : 'No lines to show.'}
          </p>
        ) : (
          <DiffBlock className={styles.diff} rows={rows}>
            <div className={styles.rows}>
              <ChunkedRows
                rows={rows}
                breaks={breaks}
                path={path}
                shebang={file?.shebang ?? null}
                selected={dragged ?? openAt}
                handlers={handlers}
                after={after}
                hold={hold}
                onName={followHere}
              />
            </div>
          </DiffBlock>
        )}
        {(file?.truncated || card.truncated) && (
          <p className={styles.note}>This file is too long to show whole. The rest is cut.</p>
        )}
      </div>
    </section>
  );
});
