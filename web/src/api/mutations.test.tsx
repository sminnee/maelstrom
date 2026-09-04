import { describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { makeAgent, makeQuestionItem, makeTask, worldWith } from '../test/fixtures';
import { createFakeServer } from '../test/fakeServer';
import { useAnswer, useApprove, useDeny, useResume, useSay, useStop } from './agents';
import { ApiProvider } from './ApiProvider';
import { useAddToDesk, useRemoveFromDesk } from './desk';
import {
  useAddComment,
  useApproveDocument,
  useRequestChanges,
  useResolveComment,
} from './documents';
import { ApiError } from './http';
import { keys } from './keys';
import { useLaunch, useSetStatus, useUpdateTask } from './tasks';

const ANCHOR = { quote: 'q', prefix: '', suffix: '', start: 0, end: 1 };

function harness() {
  // ag1 waits on r1, so the three answers have something to answer; ag2 has
  // exited, so a resume has something to bring back.
  const server = createFakeServer({
    world: worldWith({
      tasks: [makeTask({ id: 'northwind/NORT-7' })],
      agents: [
        makeAgent({ id: 'ag1', state: 'awaiting-question', pendingRequestId: 'r1' }),
        makeAgent({ id: 'ag2', state: 'exited', exitCode: 1 }),
      ],
      desk: [{ id: 'task:northwind/NORT-7', addedAt: '' }],
    }),
    transcripts: {
      ag1: {
        agentId: 'ag1',
        items: [makeQuestionItem({ requestId: 'r1' })],
        truncatedBefore: false,
      },
    },
  });
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: 0 } } });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApiProvider api={server.api} queryClient={queryClient}>
      {children}
    </ApiProvider>
  );
  return { server, invalidate, wrapper };
}

type Case = [
  name: string,
  hook: () => { mutateAsync: (vars: never) => Promise<unknown> },
  vars: unknown,
  request: string,
  body: unknown,
  invalidates: unknown[],
];

const AG = { agentId: 'ag1', requestId: 'r1' };
const agentKeys = [keys.agents.list(), keys.agents.detail('ag1'), keys.attention()];
const taskKeys = [keys.tasks.list(), keys.tasks.detail('northwind/NORT-7')];

describe('the mutation hooks', () => {
  it.each<Case>([
    ['useApprove', useApprove, AG, 'POST /api/agents/ag1/approve', { requestId: 'r1' }, agentKeys],
    [
      'useDeny',
      useDeny,
      { ...AG, reason: 'no' },
      'POST /api/agents/ag1/deny',
      { requestId: 'r1', reason: 'no' },
      agentKeys,
    ],
    [
      'useAnswer',
      useAnswer,
      { ...AG, answers: { q: 'a' } },
      'POST /api/agents/ag1/answer',
      { requestId: 'r1', answers: { q: 'a' } },
      agentKeys,
    ],
    [
      'useSay',
      useSay,
      { agentId: 'ag1', text: 'hi' },
      'POST /api/agents/ag1/say',
      { text: 'hi' },
      agentKeys,
    ],
    ['useStop', useStop, { agentId: 'ag1' }, 'POST /api/agents/ag1/stop', {}, agentKeys],
    [
      'useResume',
      useResume,
      { agentId: 'ag2' },
      'POST /api/agents/ag2/resume',
      {},
      [keys.agents.list(), keys.agents.detail('ag2'), keys.attention()],
    ],
    [
      'useLaunch',
      useLaunch,
      { taskId: 'northwind/NORT-7' },
      'POST /api/tasks/northwind/NORT-7/launch',
      {},
      [...taskKeys, keys.agents.list(), keys.desk()],
    ],
    [
      'useSetStatus',
      useSetStatus,
      { taskId: 'northwind/NORT-7', status: 'done' },
      'POST /api/tasks/northwind/NORT-7/status',
      { status: 'done' },
      taskKeys,
    ],
    [
      'useUpdateTask',
      useUpdateTask,
      { taskId: 'northwind/NORT-7', fields: { title: 'T' } },
      'PATCH /api/tasks/northwind/NORT-7',
      { title: 'T' },
      taskKeys,
    ],
    [
      'useAddToDesk',
      useAddToDesk,
      { id: 'task:a/b' },
      'POST /api/desk',
      { id: 'task:a/b' },
      [keys.desk()],
    ],
    [
      'useRemoveFromDesk',
      useRemoveFromDesk,
      { id: 'task:northwind/NORT-7' },
      'DELETE /api/desk/task%3Anorthwind%2FNORT-7',
      undefined,
      [keys.desk()],
    ],
  ])(
    '%s sends %s and invalidates what it touched',
    async (_name, hook, vars, request, body, invalidates) => {
      const { server, invalidate, wrapper } = harness();
      const { result } = renderHook(hook, { wrapper });
      await act(async () => {
        await result.current.mutateAsync(vars as never);
      });
      expect(server.requests.map((r) => `${r.method} ${r.path}`)).toEqual([request]);
      expect(server.requests[0]?.body).toEqual(body);
      expect(invalidate.mock.calls.map((c) => c[0]?.queryKey)).toEqual(invalidates);
    },
  );

  it.each<[string, () => { mutateAsync: (vars: never) => Promise<unknown> }, unknown]>([
    ['useAddComment', useAddComment, { documentId: 'd1', version: 1, anchor: ANCHOR, body: 'x' }],
    ['useResolveComment', useResolveComment, { documentId: 'd1', commentId: 'c1' }],
    ['useApproveDocument', useApproveDocument, { documentId: 'd1', version: 1 }],
    ['useRequestChanges', useRequestChanges, { documentId: 'd1', version: 1, summary: 's' }],
  ])('%s rejects with not_implemented and invalidates nothing', async (_name, hook, vars) => {
    const { invalidate, wrapper } = harness();
    const { result } = renderHook(hook, { wrapper });
    const err = await result.current.mutateAsync(vars as never).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 501, code: 'not_implemented' });
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('a refusal rejects with the code and the message, and invalidates nothing', async () => {
    const { server, invalidate, wrapper } = harness();
    server.refuse(/approve/, { status: 409, code: 'stale_request', message: 'stale' });
    const { result } = renderHook(useApprove, { wrapper });
    const err = await result.current.mutateAsync(AG).catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 409, code: 'stale_request', message: 'stale' });
    expect(invalidate).not.toHaveBeenCalled();
  });
});
