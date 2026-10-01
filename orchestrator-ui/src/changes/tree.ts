import type { FileDiff } from '../protocol/entities';

export type TreeNode =
  | { kind: 'dir'; name: string; path: string; children: TreeNode[] }
  | { kind: 'file'; name: string; path: string; file: FileDiff };

type Dir = Extract<TreeNode, { kind: 'dir' }>;

/**
 * A diff's files as a directory tree. Directories sort before files, and each
 * level sorts by name. A chain of directories that each hold one directory and
 * nothing else joins into one node, `docs/specs/agent`, as GitHub draws it.
 */
export function fileTree(files: FileDiff[]): TreeNode[] {
  const root: Dir = { kind: 'dir', name: '', path: '', children: [] };
  for (const file of files) {
    const parts = file.path.split('/');
    const name = parts.pop() ?? file.path;
    let dir = root;
    for (const part of parts) {
      const path = dir.path ? `${dir.path}/${part}` : part;
      let next = dir.children.find((n): n is Dir => n.kind === 'dir' && n.path === path);
      if (!next) {
        next = { kind: 'dir', name: part, path, children: [] };
        dir.children.push(next);
      }
      dir = next;
    }
    dir.children.push({ kind: 'file', name, path: file.path, file });
  }
  return settle(root.children);
}

/** Join the one-directory chains and sort, at every level. */
function settle(nodes: TreeNode[]): TreeNode[] {
  return nodes.map(join).sort(inOrder);
}

function join(node: TreeNode): TreeNode {
  if (node.kind === 'file') return node;
  const [only, ...rest] = node.children;
  if (only?.kind === 'dir' && rest.length === 0)
    return join({ ...only, name: `${node.name}/${only.name}` });
  return { ...node, children: settle(node.children) };
}

function inOrder(a: TreeNode, b: TreeNode): number {
  if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1;
  return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
}
