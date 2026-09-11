import { screen } from '@testing-library/react';
import type { Attention } from '../protocol/attention';
import type { Document } from '../protocol/documents';
import type { FakeServer } from './fakeServer';

/**
 * Reading and moving a rendered app, for the `App*.test.tsx` files.
 *
 * These sit beside `renderApp` rather than inside it: `renderApp` mounts the
 * app, and these read the DOM it produced or push the world past it.
 */

/** The one expanded node, as the card it grew into. */
export const expanded = () => screen.getByRole('dialog');

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
    title: 'plan.md',
    markdown: '# Migrate to Postgres 16\n\nCarefully.\n',
    version: 1,
    status,
    source: { type: 'plan_review', requestId: 'req-nort9-plan', planFilePath: '' },
  };
  server.change({ kind: 'document', ids: [doc.id] }, (w) => {
    w.documents[doc.id] = doc;
  });
}
