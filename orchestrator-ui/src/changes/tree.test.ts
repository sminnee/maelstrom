import { describe, expect, it } from 'vitest';
import type { FileDiff } from '../protocol/entities';
import { fileTree, type TreeNode } from './tree';

const file = (path: string): FileDiff => ({
  path,
  oldPath: null,
  status: 'modified',
  binary: false,
  additions: 0,
  deletions: 0,
  truncated: false,
  hunks: [],
});

/** The tree as indented names, a directory ending in `/`. */
const drawn = (nodes: TreeNode[], depth = 0): string[] =>
  nodes.flatMap((n) => [
    `${'  '.repeat(depth)}${n.name}${n.kind === 'dir' ? '/' : ''}`,
    ...(n.kind === 'dir' ? drawn(n.children, depth + 1) : []),
  ]);

describe('fileTree', () => {
  it('nests files under their directories, directories first, each level by name', () => {
    const tree = fileTree(
      ['zeta.md', 'src/b.ts', 'src/ui/Button.tsx', 'src/a.ts', 'README.md', 'lib/x.py'].map(file),
    );
    expect(drawn(tree)).toEqual([
      'lib/',
      '  x.py',
      'src/',
      '  ui/',
      '    Button.tsx',
      '  a.ts',
      '  b.ts',
      'README.md',
      'zeta.md',
    ]);
  });

  it('joins a chain of directories with one child each into one node', () => {
    const tree = fileTree(
      ['docs/specs/agent/a.md', 'docs/specs/agent/b.md', 'web/src/only.ts'].map(file),
    );
    expect(drawn(tree)).toEqual(['docs/specs/agent/', '  a.md', '  b.md', 'web/src/', '  only.ts']);
    expect(tree.map((n) => n.path)).toEqual(['docs/specs/agent', 'web/src']);
  });

  it('keeps a directory that holds a file and a directory as its own node', () => {
    const tree = fileTree(['docs/index.md', 'docs/specs/a.md'].map(file));
    expect(drawn(tree)).toEqual(['docs/', '  specs/', '    a.md', '  index.md']);
  });

  it('keeps a file and a directory of one path as two nodes', () => {
    const tree = fileTree(['auth', 'auth/tokens.py'].map(file));
    expect(drawn(tree)).toEqual(['auth/', '  tokens.py', 'auth']);
  });
});
