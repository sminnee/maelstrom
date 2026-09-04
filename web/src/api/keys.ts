/**
 * Every query key, in one place. A list key is a prefix of no detail key, so
 * invalidating `tasks.all()` reaches both and invalidating `tasks.list()`
 * reaches only the list.
 */
export const keys = {
  projects: () => ['projects'] as const,
  worktrees: () => ['worktrees'] as const,
  desk: () => ['desk'] as const,
  attention: () => ['attention'] as const,
  tasks: {
    all: () => ['tasks'] as const,
    list: () => ['tasks', 'list'] as const,
    detail: (id: string) => ['tasks', 'detail', id] as const,
  },
  agents: {
    all: () => ['agents'] as const,
    list: () => ['agents', 'list'] as const,
    detail: (id: string) => ['agents', 'detail', id] as const,
  },
  documents: {
    all: () => ['documents'] as const,
    list: () => ['documents', 'list'] as const,
    detail: (id: string) => ['documents', 'detail', id] as const,
  },
};
