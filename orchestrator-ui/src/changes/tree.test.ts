import { describe, expect, it } from 'vitest';
import { fileTree, type TreeNode } from './tree';

/** The tree as indented names, a directory ending in `/`. */
const drawn = (nodes: TreeNode[], depth = 0): string[] =>
  nodes.flatMap((n) => [
    `${'  '.repeat(depth)}${n.name}${n.kind === 'dir' ? '/' : ''}`,
    ...(n.kind === 'dir' ? drawn(n.children, depth + 1) : []),
  ]);

describe('fileTree', () => {
  it('gives each file its status in the diff, and none to a file the diff leaves alone', () => {
    const tree = fileTree(['a.py', 'b.py'], new Map([['a.py', 'added' as const]]));
    expect(tree.map((n) => (n.kind === 'file' ? [n.name, n.status] : []))).toEqual([
      ['a.py', 'added'],
      ['b.py', null],
    ]);
  });

  it('nests files under their directories, directories first, each level by name', () => {
    const tree = fileTree(
      ['zeta.md', 'src/b.ts', 'src/ui/Button.tsx', 'src/a.ts', 'README.md', 'lib/x.py'],
      new Map(),
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
      ['docs/specs/agent/a.md', 'docs/specs/agent/b.md', 'web/src/only.ts'],
      new Map(),
    );
    expect(drawn(tree)).toEqual(['docs/specs/agent/', '  a.md', '  b.md', 'web/src/', '  only.ts']);
    expect(tree.map((n) => n.path)).toEqual(['docs/specs/agent', 'web/src']);
  });

  it('keeps a directory that holds a file and a directory as its own node', () => {
    const tree = fileTree(['docs/index.md', 'docs/specs/a.md'], new Map());
    expect(drawn(tree)).toEqual(['docs/', '  specs/', '    a.md', '  index.md']);
  });

  it('keeps a file and a directory of one path as two nodes', () => {
    const tree = fileTree(['auth', 'auth/tokens.py'], new Map());
    expect(drawn(tree)).toEqual(['auth/', '  tokens.py', 'auth']);
  });
});
