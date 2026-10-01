import { type CSSProperties, useState } from 'react';
import type { FileDiff } from '../protocol/entities';
import { STATUS_LETTER } from './fileStatus';
import { fileTree, type TreeNode } from './tree';
import styles from './FileTree.module.css';

/**
 * A diff's files as a directory tree. A directory folds; a file calls `onPick`
 * with its path. Every directory is open at first. A file can give way to a
 * directory of the same path in one diff, so a key names the kind too.
 */
export function FileTree({ files, onPick }: { files: FileDiff[]; onPick: (path: string) => void }) {
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const toggle = (path: string) =>
    setFolded((was) => {
      const now = new Set(was);
      if (!now.delete(path)) now.add(path);
      return now;
    });

  const level = (nodes: TreeNode[], depth: number) =>
    nodes.map((node) => {
      const indent = { '--depth': depth } as CSSProperties;
      if (node.kind === 'file')
        return (
          <li key={`file:${node.path}`} role="none">
            <button
              type="button"
              role="treeitem"
              className={styles.item}
              style={indent}
              title={node.path}
              onClick={() => onPick(node.path)}
            >
              <span className={styles.status} data-status={node.file.status}>
                {STATUS_LETTER[node.file.status]}
              </span>
              <span className={styles.name}>{node.name}</span>
            </button>
          </li>
        );
      const open = !folded.has(node.path);
      return (
        <li key={`dir:${node.path}`} role="none">
          <button
            type="button"
            role="treeitem"
            aria-expanded={open}
            className={styles.item}
            style={indent}
            title={node.path}
            onClick={() => toggle(node.path)}
          >
            <span className={styles.chevron} aria-hidden="true" />
            <span className={styles.name}>{node.name}</span>
          </button>
          {open && <ul role="group">{level(node.children, depth + 1)}</ul>}
        </li>
      );
    });

  return (
    <ul role="tree" aria-label="Changed files" className={styles.tree}>
      {level(fileTree(files), 0)}
    </ul>
  );
}
