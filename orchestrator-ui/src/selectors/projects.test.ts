import { describe, expect, it } from 'vitest';
import { makeTask, makeWorktree, worldWith } from '../fake/fixtures';
import { hasUserTasks, projectPaths } from './projects';

describe('projectPaths', () => {
  const world = worldWith({
    worktrees: [
      makeWorktree({ id: 'askastro-alpha', project: 'askastro', nato: 'alpha' }),
      makeWorktree({
        id: 'askastro-_main',
        project: 'askastro',
        nato: '_main',
        path: '/Users/sam/Projects/askastro/_main',
      }),
    ],
  });

  it("derives the worktree range from _main's folder, with home as ~", () => {
    expect(projectPaths(world, 'askastro', '/Users/sam')).toEqual({
      main: '~/Projects/askastro/_main',
      worktrees: '~/Projects/askastro/askastro-alpha..zulu',
    });
  });

  it('leaves a path outside home whole, and a home that is only a prefix alone', () => {
    expect(projectPaths(world, 'askastro', '/Users/sa')?.main).toBe(
      '/Users/sam/Projects/askastro/_main',
    );
    expect(projectPaths(world, 'askastro', undefined)?.main).toBe(
      '/Users/sam/Projects/askastro/_main',
    );
  });

  it('is null until _main has loaded', () => {
    expect(projectPaths(world, 'northwind', '/Users/sam')).toBeNull();
  });
});

describe('hasUserTasks', () => {
  const setUp = makeTask({ id: 'A-1', project: 'a', command: 'setup-project', status: 'done' });

  it('does not count the set-up task', () => {
    expect(hasUserTasks(worldWith({ tasks: [setUp] }), 'a')).toBe(false);
  });

  it("counts any other task of the project, in any status, and no other project's", () => {
    for (const status of ['done', 'template'] as const) {
      const own = makeTask({ id: 'A-2', project: 'a', status });
      expect(hasUserTasks(worldWith({ tasks: [setUp, own] }), 'a')).toBe(true);
    }
    expect(hasUserTasks(worldWith({ tasks: [makeTask({ project: 'b' })] }), 'a')).toBe(false);
  });
});
