import type { Attention } from '../protocol/attention';
import type { Document } from '../protocol/documents';
import type { FakeServer } from './fakeServer';

/**
 * Moves of the seed world, as the server would make them. A test calls one to
 * push the world past a mounted app. Each one names ids of the `desk` seed.
 */

/**
 * End the turn of MAEL-40.1's agent, as the server would: it has said
 * something, its task is unfinished, and it holds no open ask.
 */
export function endTurn(server: FakeServer) {
  server.change({ kind: 'agent', ids: ['c3e8f1b5'] }, (w) => {
    w.agents['c3e8f1b5'] = { ...w.agents['c3e8f1b5']!, state: 'idle' };
  });
}

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

/** A release note on NORT-7: a document that is neither a plan nor a verification. */
export function addNote(server: FakeServer) {
  const doc: Document = {
    id: 'doc-new-note',
    agentId: 'a1f3c9e2',
    taskId: 'NORT-7',
    kind: 'other',
    title: 'Release note',
    markdown: 'The exporter is faster.\n',
    version: 1,
    status: 'draft',
    source: { type: 'draft_file', fileId: 'new-note', filename: '.drafts/note.md' },
    group: { id: 'doc-new-note', title: 'Release note', position: 0 },
  };
  server.change({ kind: 'document', ids: [doc.id] }, (w) => {
    w.documents[doc.id] = doc;
  });
}

/**
 * An attached verification on NORT-7, as a restarted server seeds one: no agent, and
 * media served from the notebook. The id carries `new`, so a test finds this
 * document and not one the seed holds.
 */
export function addAttachedVerification(server: FakeServer) {
  const doc: Document = {
    id: 'attached-new-verification',
    agentId: '',
    taskId: 'NORT-7',
    kind: 'verification',
    title: 'Login flow',
    markdown:
      '# Login flow\n\n![The flow](/api/attachments/northwind/NORT-7/flow.webm)\n\nIt works.\n',
    version: 1,
    status: 'draft',
    source: { type: 'draft_file', fileId: null, filename: '.drafts/verification.md' },
    group: { id: 'attached-new-verification', title: 'Login flow', position: 0 },
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
