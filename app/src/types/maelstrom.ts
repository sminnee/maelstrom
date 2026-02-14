export interface Worktree {
  name: string;
  folder: string;
  path: string;
  branch: string | null;
  is_closed: boolean;
  dirty_files: number;
  local_commits: number;
  pr_number: number | null;
  pr_commits: number | null;
  pushed_commits: number | null;
  ide_active: boolean;
}

export interface Project {
  name: string;
  path: string;
  worktrees: Worktree[];
}

export interface ListAllResponse {
  projects: Project[];
}
