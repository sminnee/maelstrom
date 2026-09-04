import { useMemo } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { byId, type WorldView } from '../selectors/world';
import { useAgents } from './agents';
import { useAttention } from './attention';
import { useDesk } from './desk';
import { useDocuments } from './documents';
import { ApiError } from './http';
import { useProjects } from './projects';
import { useTasks } from './tasks';
import { useWorktrees } from './worktrees';

export type WorldStatus = 'loading' | 'error' | 'ready';

export interface WorldRead {
  world: WorldView;
  /** `ready` once every required table has data; a later refetch failure keeps the data. */
  status: WorldStatus;
  errors: ApiError[];
  retry: () => void;
}

/**
 * The world, composed from the seven list queries. Each table is rebuilt only
 * when its query's data changes identity, and TanStack keeps that identity
 * across a refetch that changed nothing, so an unchanged world is the same
 * object and nothing under it re-renders.
 *
 * `ready` needs the six tables the canvas draws from. Documents are the
 * seventh: a lane can draw without them.
 */
export function useWorld(): WorldRead {
  const projects = useProjects();
  const worktrees = useWorktrees();
  const tasks = useTasks();
  const agents = useAgents();
  const attention = useAttention();
  const desk = useDesk();
  const documents = useDocuments();

  const projectTable = useMemo(() => byId(projects.data?.projects), [projects.data]);
  const worktreeTable = useMemo(() => byId(worktrees.data?.worktrees), [worktrees.data]);
  const taskTable = useMemo(() => byId(tasks.data?.tasks), [tasks.data]);
  const agentTable = useMemo(() => byId(agents.data?.agents), [agents.data]);
  const attentionTable = useMemo(() => byId(attention.data?.attention), [attention.data]);
  const deskTable = useMemo(() => byId(desk.data?.desk), [desk.data]);
  const documentTable = useMemo(() => byId(documents.data?.documents), [documents.data]);

  const world = useMemo<WorldView>(
    () => ({
      projects: projectTable,
      worktrees: worktreeTable,
      tasks: taskTable,
      agents: agentTable,
      attention: attentionTable,
      desk: deskTable,
      documents: documentTable,
    }),
    [projectTable, worktreeTable, taskTable, agentTable, attentionTable, deskTable, documentTable],
  );

  const required: UseQueryResult[] = [projects, worktrees, tasks, agents, attention, desk];
  const status: WorldStatus = required.every((q) => q.data !== undefined)
    ? 'ready'
    : required.some((q) => q.isError)
      ? 'error'
      : 'loading';
  // Only the tables that block the render: a documents failure never does.
  const errors = required.map((q) => q.error).filter((e): e is ApiError => e instanceof ApiError);
  const retry = () => {
    for (const q of [...required, documents]) if (q.isError) void q.refetch();
  };

  return { world, status, errors, retry };
}
