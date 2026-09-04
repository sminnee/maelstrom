import { deskIdForTask } from '../protocol/deskId';
import type { Agent, DeskEntry, Project, Task, Worktree } from '../protocol/entities';
import type { Attention } from '../protocol/attention';
import type { Document } from '../protocol/documents';
import type { PermissionRequestItem, PlanReviewItem, QuestionItem } from '../protocol/transcript';
import { emptyFakeWorld, type FakeWorld } from './fakeServer';

export function makeProject(over: Partial<Project> = {}): Project {
  return { id: 'northwind', name: 'northwind', stackTip: 'main', ...over };
}

export function makeWorktree(over: Partial<Worktree> = {}): Worktree {
  return {
    id: 'northwind-alpha',
    project: 'northwind',
    nato: 'alpha',
    path: '/Users/dev/Projects/northwind/northwind-alpha',
    branch: 'feat/orders',
    base: 'main',
    isClosed: false,
    dirtyFiles: 0,
    localCommits: 0,
    prNumber: null,
    appUrl: '',
    appRunning: false,
    sessionCount: 0,
    ...over,
  };
}

export function makeTask(over: Partial<Task> = {}): Task {
  return {
    id: 'NORT-7',
    notebookId: 'NORT-7',
    project: 'northwind',
    title: 'Add order export',
    status: 'todo',
    command: '',
    mode: 'auto',
    branch: 'feat/orders',
    parent: '',
    follows: [],
    priority: 'medium',
    model: '',
    base: '',
    content: '',
    steps: [],
    log: [],
    created: '2026-09-01T00:00:00Z',
    updated: '2026-09-01T00:00:00Z',
    actionable: true,
    ...over,
  };
}

export function makeAgent(over: Partial<Agent> = {}): Agent {
  return {
    id: 'agent-1',
    state: 'processing',
    session: 'sess-1',
    cwd: '/Users/dev/Projects/northwind/northwind-alpha',
    model: 'claude-opus-5',
    permissionMode: 'normal',
    waitingOn: '',
    lastMessage: '',
    costUsd: 0,
    taskId: 'NORT-7',
    project: 'northwind',
    worktreeId: 'northwind-alpha',
    exitCode: null,
    pendingRequestId: null,
    ...over,
  };
}

export function makeDocument(over: Partial<Document> = {}): Document {
  return {
    id: 'doc-1',
    agentId: 'agent-1',
    taskId: 'NORT-7',
    kind: 'plan',
    title: 'plan.md',
    markdown: '# Plan\n\nDo the thing.\n',
    version: 1,
    status: 'awaiting-review',
    source: { type: 'plan_review', requestId: 'req-1', planFilePath: '' },
    ...over,
  };
}

export function makePermissionRequest(
  over: Partial<PermissionRequestItem> = {},
): PermissionRequestItem {
  return {
    id: 'p1',
    ts: '',
    type: 'permission_request',
    requestId: 'req-1',
    tool: 'Write',
    input: { file_path: '/tmp/hello.txt' },
    description: 'Write hello.txt',
    ...over,
  };
}

export function makeQuestionItem(over: Partial<QuestionItem> = {}): QuestionItem {
  return { id: 'q1', ts: '', type: 'question', requestId: 'req-1', questions: [], ...over };
}

export function makePlanReview(over: Partial<PlanReviewItem> = {}): PlanReviewItem {
  return { id: 'pr1', ts: '', type: 'plan_review', requestId: 'req-1', documentId: null, ...over };
}

export function makeAttention(over: Partial<Attention> = {}): Attention {
  return {
    id: 'att-1',
    kind: 'plan_review',
    agentId: 'agent-1',
    taskId: 'NORT-7',
    documentId: null,
    requestId: 'req-1',
    summary: 'Plan awaiting review',
    raisedAt: '2026-09-01T00:00:00Z',
    clearedAt: null,
    ...over,
  };
}

export function makeDeskEntry(over: Partial<DeskEntry> = {}): DeskEntry {
  return { id: deskIdForTask('NORT-7'), addedAt: '2026-09-01T00:00:00Z', ...over };
}

/** Desk entries for every one of `tasks`, for a world drawn whole. */
export function onDesk(tasks: Task[]): DeskEntry[] {
  return tasks.map((t) => makeDeskEntry({ id: deskIdForTask(t.id) }));
}

/** A world holding the given entities, keyed by id. A `WorldView` reads it as-is. */
export function worldWith(parts: {
  projects?: Project[];
  worktrees?: Worktree[];
  tasks?: Task[];
  agents?: Agent[];
  documents?: Document[];
  attention?: Attention[];
  desk?: DeskEntry[];
}): FakeWorld {
  const world = emptyFakeWorld();
  for (const p of parts.projects ?? []) world.projects[p.id] = p;
  for (const w of parts.worktrees ?? []) world.worktrees[w.id] = w;
  for (const t of parts.tasks ?? []) world.tasks[t.id] = t;
  for (const a of parts.agents ?? []) world.agents[a.id] = a;
  for (const d of parts.documents ?? []) world.documents[d.id] = d;
  for (const a of parts.attention ?? []) world.attention[a.id] = a;
  for (const e of parts.desk ?? []) world.desk[e.id] = e;
  return world;
}
