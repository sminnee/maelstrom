import { screen, within } from '@testing-library/react';
import type { Attention } from '../protocol/attention';
import type { Document } from '../protocol/documents';
import type { UserEvent } from '@testing-library/user-event';
import type { FakeServer } from './fakeServer';
import { clickNode } from './renderApp';

/**
 * Reading and moving a rendered app, for the `App*.test.tsx` files.
 *
 * These sit beside `renderApp` rather than inside it: `renderApp` mounts the
 * app, and these read the DOM it produced or push the world past it.
 */

/** The one expanded node, as the card it grew into. */
export const expanded = () => screen.getByRole('dialog');

/** The panel's tab strip: the tabs of the worktree in view. */
export const tabStrip = () => screen.getByRole('tablist', { name: 'Open tabs' });

/** The panel's sidebar row for a worktree, named `<project> <nato>`. */
export const worktreeRow = (name: string) =>
  within(screen.getByRole('tablist', { name: 'Worktrees' })).getByRole('tab', { name });

/**
 * The active tab's body. Not found by role alone: the worktree group around it
 * is the sidebar's tabpanel, so the page holds two.
 */
export const tabBody = () => screen.getByTestId('panel-body');

/** The keys of the tabs in the strip, in strip order. */
export const stripKeys = () =>
  within(tabStrip())
    .getAllByRole('tab')
    .map((t) => t.getAttribute('data-tab-key'));

/** Open a node's session from its expanded card. */
export async function openSession(user: UserEvent, taskId: string) {
  clickNode(taskId);
  await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
}

/** How many items the attention chip counts. */
export const chipCount = () =>
  Number(screen.getByTestId('attention-chip').textContent?.replace(/\D/g, ''));

/** The `data-state` a task's node draws with, or undefined when it draws none. */
export const nodeState = (taskId: string) =>
  document.querySelector(`[data-task-id="${taskId}"]`)?.getAttribute('data-state');

/** Park NORT-9's agent on a question, as the server would after a control_request. */
export function askQuestion(server: FakeServer) {
  const requestId = 'req-nort9-q';
  server.append('d9a4c7f1', {
    id: 'd9a4c7f1-q',
    ts: '',
    type: 'question',
    requestId,
    questions: [
      {
        question: 'Which columns?',
        header: 'Columns',
        multiSelect: true,
        options: [
          { label: 'Id', description: '' },
          { label: 'Total', description: '' },
        ],
      },
      {
        question: 'Stream or batch?',
        header: 'Export',
        multiSelect: false,
        options: [
          { label: 'Stream', description: '' },
          { label: 'Batch', description: '' },
        ],
      },
    ],
  });
  const attention: Attention = {
    id: 'att-nort9-q',
    kind: 'question',
    agentId: 'd9a4c7f1',
    taskId: 'NORT-9',
    documentId: null,
    requestId,
    summary: 'Which columns?',
    raisedAt: '2026-09-02T09:00:00.000Z',
    clearedAt: null,
  };
  server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
    w.agents['d9a4c7f1'] = {
      ...w.agents['d9a4c7f1']!,
      state: 'awaiting-question',
      pendingRequestIds: [requestId],
      waitingOn: 'Which columns?',
    };
    w.attention[attention.id] = attention;
  });
  server.change({ kind: 'attention', ids: [attention.id] });
}

/** Give NORT-9 a plan document, as a plan review would. */
export function addPlan(server: FakeServer, status: Document['status'] = 'approved') {
  const doc: Document = {
    id: 'doc-nort9-plan',
    agentId: 'd9a4c7f1',
    taskId: 'NORT-9',
    kind: 'plan',
    title: 'Plan',
    markdown: '# Migrate to Postgres 16\n\nCarefully.\n',
    version: 1,
    status,
    source: { type: 'plan_review', requestId: 'req-nort9-plan', planFilePath: '' },
    group: { id: 'doc-nort9-plan', title: 'Plan', position: 0 },
  };
  server.change({ kind: 'document', ids: [doc.id] }, (w) => {
    w.documents[doc.id] = doc;
  });
}

/** NORT-12's agent presents a three-draft task set as one tag, as review group `grp-set`. */
export function addTaskSet(
  server: FakeServer,
  over: Partial<Record<number, Partial<Document>>> = {},
) {
  const names = ['Execute: parse', 'Execute: mint', 'Execute: show'];
  const docs: Document[] = names.map((title, i) => ({
    id: `doc-set-${i}`,
    agentId: 'e5b1d8c3',
    taskId: 'NORT-12',
    kind: 'tasks',
    title,
    markdown: `## ${title}\n\nStep ${i + 1}.\n`,
    version: 1,
    status: 'awaiting-review',
    source: { type: 'draft_file', fileId: `set-${i}`, filename: `.drafts/set-${i}.md` },
    group: { id: 'grp-set', title: 'Iteration 3', position: i },
    ...over[i],
  }));
  server.change({ kind: 'document', ids: docs.map((d) => d.id) }, (w) => {
    for (const d of docs) w.documents[d.id] = d;
  });
}

/** The commands sent since request `from`: every call but a read, path decoded. */
export function commandsSince(server: FakeServer, from: number): string[] {
  return server.requests
    .slice(from)
    .filter((r) => r.method !== 'GET')
    .map((r) => `${r.method} ${decodeURIComponent(r.path)}`);
}
