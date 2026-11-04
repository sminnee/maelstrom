import { type CSSProperties, memo, useMemo, useState } from 'react';
import { useWorktreeTree } from '../api/worktreeChanges';
import type { FileDiff } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { actionIcon } from '../ui/actionIcons';
import { SegmentedControl } from '../ui/SegmentedControl';
import { STATUS_LETTER } from './fileStatus';
import { fileTree, type TreeNode } from './tree';
import styles from './FileTree.module.css';

type Shown = 'changed' | 'all';

const SHOWN = [
  { id: 'changed' as const, label: 'Changed' },
  { id: 'all' as const, label: 'All' },
];

/** The most matches a search lists. */
const MATCH_CAP = 200;

/**
 * A rev's files as a directory tree: the files the diff changes, or every
 * file of the rev's new side. A search replaces the tree with the matching
 * paths. A directory folds; a file calls `onPick` with its path. Every
 * directory is shut at first. A file can give way to a directory of the same
 * path in one diff, so a key names the kind too.
 */
export const FileTree = memo(function FileTree({
  worktreeId,
  rev,
  files,
  pinned,
  onPick,
}: {
  worktreeId: WorktreeId;
  rev: string;
  files: FileDiff[];
  /** The paths with a pin, which the list marks. */
  pinned: ReadonlySet<string>;
  onPick: (path: string) => void;
}) {
  const [shown, setShown] = useState<Shown>('changed');
  const [search, setSearch] = useState('');
  const all = useWorktreeTree(worktreeId, rev, { enabled: shown === 'all' });
  const status = useMemo(() => new Map(files.map((f) => [f.path, f.status])), [files]);
  const paths = useMemo(
    () => (shown === 'all' ? (all.data?.paths ?? []) : files.map((f) => f.path)),
    [shown, all.data, files],
  );
  const tree = useMemo(() => fileTree(paths, status), [paths, status]);
  const needle = search.trim().toLowerCase();
  const matches = useMemo(
    () => (needle ? paths.filter((p) => p.toLowerCase().includes(needle)) : []),
    [needle, paths],
  );
  const [opened, setOpened] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = (path: string) =>
    setOpened((was) => {
      const now = new Set(was);
      if (!now.delete(path)) now.add(path);
      return now;
    });

  const fileItem = (path: string, name: string, depth: number) => {
    const letter = status.get(path);
    return (
      <li key={`file:${path}`} role="none">
        <button
          type="button"
          role="treeitem"
          className={styles.item}
          style={{ '--depth': depth } as CSSProperties}
          title={path}
          data-pinned={pinned.has(path) || undefined}
          onClick={() => onPick(path)}
        >
          <span className={styles.status} data-status={letter}>
            {letter && STATUS_LETTER[letter]}
          </span>
          <span className="truncate">{name}</span>
          {pinned.has(path) && actionIcon('pin', styles.pin)}
        </button>
      </li>
    );
  };

  const level = (nodes: TreeNode[], depth: number) =>
    nodes.map((node) => {
      if (node.kind === 'file') return fileItem(node.path, node.name, depth);
      const open = opened.has(node.path);
      return (
        <li key={`dir:${node.path}`} role="none">
          <button
            type="button"
            role="treeitem"
            aria-expanded={open}
            className={styles.item}
            style={{ '--depth': depth } as CSSProperties}
            title={node.path}
            onClick={() => toggle(node.path)}
          >
            {actionIcon(open ? 'folderOpen' : 'folder', styles.folder)}
            <span className="truncate">{node.name}</span>
          </button>
          {open && <ul role="group">{level(node.children, depth + 1)}</ul>}
        </li>
      );
    });

  return (
    <div className={styles.files}>
      <div className={styles.controls}>
        <SegmentedControl label="Files to list" items={SHOWN} pressed={shown} onPress={setShown} />
        <input
          type="search"
          className={styles.search}
          aria-label="Search files"
          placeholder="Search files"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <ul
        role="tree"
        aria-label={shown === 'all' ? 'All files' : 'Changed files'}
        className={styles.tree}
      >
        {needle
          ? matches.slice(0, MATCH_CAP).map((path) => fileItem(path, path, 0))
          : level(tree, 0)}
      </ul>
      {matches.length > MATCH_CAP && (
        <p className={styles.cap}>
          {MATCH_CAP} of {matches.length}
        </p>
      )}
      {shown === 'all' && all.isError && (
        <p className={styles.cap} role="alert">
          Could not list the files: {all.error.message}
        </p>
      )}
    </div>
  );
});
