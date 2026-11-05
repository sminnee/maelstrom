import type { Project } from '../protocol/entities';
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
