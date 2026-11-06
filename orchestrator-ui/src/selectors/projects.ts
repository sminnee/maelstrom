import type { Project } from '../protocol/entities';
import type { ProjectId } from '../protocol/ids';
import { tildePath } from '../protocol/paths';
import type { WorldView } from './world';

/** One row of the Projects view. */
export interface ProjectRow extends Project {
  /** The worktrees of the project that are not closed, `_main` among them. */
  openWorktrees: number;
}

/** The projects, by name, each with its open worktree count. */
export function listProjects(world: Pick<WorldView, 'projects' | 'worktrees'>): ProjectRow[] {
  const open = new Map<string, number>();
  for (const w of Object.values(world.worktrees)) {
    if (!w.isClosed) open.set(w.project, (open.get(w.project) ?? 0) + 1);
  }
  return Object.values(world.projects)
    .map((p) => ({ ...p, openWorktrees: open.get(p.id) ?? 0 }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Where a project lives on disk: its `_main` checkout, and the range its worktrees take. */
export interface ProjectPaths {
  main: string;
  /** The worktree folders, as a range: `…/askastro/askastro-alpha..zulu`. */
  worktrees: string;
}

/**
 * A project's paths, derived from its `_main`, with `home` written as `~`. `_main` cannot be
 * closed, so every project has one; `null` until the worktrees load.
 */
export function projectPaths(
  world: Pick<WorldView, 'worktrees'>,
  projectId: ProjectId,
  home: string | undefined,
): ProjectPaths | null {
  const main = Object.values(world.worktrees).find(
    (w) => w.project === projectId && w.nato === '_main',
  );
  if (!main) return null;
  const folder = main.path.slice(0, main.path.lastIndexOf('/'));
  return {
    main: tildePath(main.path, home),
    worktrees: tildePath(`${folder}/${projectId}-alpha..zulu`, home),
  };
}

/** The command of the task that sets a project up. It is not the user's own work. */
export const SETUP_COMMAND = 'setup-project';

/** Whether a project holds a task of the user's own: any but the set-up, in any status. */
export function hasUserTasks(world: Pick<WorldView, 'tasks'>, projectId: ProjectId): boolean {
  return Object.values(world.tasks).some(
    (t) => t.project === projectId && t.command !== SETUP_COMMAND,
  );
}
